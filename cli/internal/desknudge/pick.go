package desknudge

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/doit/cli/internal/programs"
	"github.com/datapointchris/doit/cli/internal/pursuits"
	"github.com/datapointchris/doit/cli/internal/service"
)

// The exit codes of a pick, which `doit desk-nudge panel pick` exits with.
const (
	PickDone    = 0
	PickFailed  = 1
	PickRefused = 2
)

// PickError is a pick that did nothing or did not go through. Its notification
// is already shown, and its picked line written.
type PickError struct {
	ExitCode int    `json:"exit_code"`
	Message  string `json:"message"`
}

func (p *PickError) Error() string { return p.Message }

func refused(format string, args ...any) *PickError {
	return &PickError{ExitCode: PickRefused, Message: fmt.Sprintf(format, args...)}
}

// completion is how one completion through its owner ended, as its notification says it.
type completion struct {
	ok       bool
	summary  string
	body     string
	exitCode int
	// recordedNothing is the owner known to have recorded nothing, so the row can be picked again at once.
	recordedNothing bool
	// skipped is a habit found done today at its commit, so nothing ran.
	skipped bool
}

// The outcomes of a pick. A refused pick recorded nothing, so its row is free
// again. An uncertain one may have landed, so its row is held until the next
// read.
const (
	OutcomeDone      = "done"
	OutcomeSkipped   = "skipped"
	OutcomeRefused   = "refused"
	OutcomeUncertain = "uncertain"
)

func (c completion) outcome() string {
	switch {
	case c.ok:
		return OutcomeDone
	case c.skipped:
		return OutcomeSkipped
	case c.recordedNothing:
		return OutcomeRefused
	}
	return OutcomeUncertain
}

var habitID = regexp.MustCompile(`^[0-9]+$`)

// pick acts on one panel row by its key, and answers the next step: a done
// step once something is completed, or a timed pursuit's minutes. minutes
// logs a timed pursuit at once.
func (w *Watch) pick(ctx context.Context, key string, minutes *int, notifyDone bool) (Panel, error) {
	d := w.desk
	panel := panelFor(d.Paths.readPanel(), d.Paths.readState(), StepToday)
	resolved, refusal := d.resolveKey(&panel, key, minutes)
	if refusal != nil {
		return d.refuse(ctx, refusal)
	}
	switch resolved.verb {
	case verbAnswer:
		return d.pickedAnswer(ctx, resolved.answer, resolved.nudgeID)
	case verbAdjust:
		return minutesPanel(panel, resolved.found, resolved.minutes), nil
	case verbMinutes:
		return minutesPanel(panel, resolved.found, nil), nil
	}
	return w.complete(ctx, resolved.found, resolved.minutes, notifyDone)
}

// keyVerb is what a picked key asks for.
type keyVerb int

const (
	// verbComplete completes its row.
	verbComplete keyVerb = iota
	// verbAnswer answers the nudge.
	verbAnswer
	// verbAdjust reopens a minutes step at another amount.
	verbAdjust
	// verbMinutes opens a timed row's minutes step, because the pick names none.
	verbMinutes
)

// resolvedKey is what a key names: an answer, or a row with the minutes it logs.
type resolvedKey struct {
	verb            keyVerb
	answer, nudgeID string
	found           entry
	minutes         *int
}

// resolveKey reads a picked key. The watch owns the vocabulary of keys. An
// answer key and a minutes key are read as the watch wrote them, and any other
// key is resolved against the panel's parts, whose completes says what to run.
func (d *Desk) resolveKey(panel *Panel, key string, minutes *int) (resolvedKey, *PickError) {
	kind, rest, _ := strings.Cut(key, keySeparator)
	switch kind {
	case KindAnswer:
		answer, nudgeID, _ := strings.Cut(rest, keySeparator)
		if !IsAnswer(answer) {
			return resolvedKey{}, refused("%q is not an answer", answer)
		}
		return resolvedKey{verb: verbAnswer, answer: answer, nudgeID: nudgeID}, nil
	case KindLogMinutes, KindAdjustMinutes:
		amount, rowKey, _ := strings.Cut(rest, keySeparator)
		logged, err := strconv.Atoi(amount)
		if err != nil || logged < 1 {
			return resolvedKey{}, refused("%s names no whole number of minutes", key)
		}
		found, refusal := d.listed(panel, rowKey)
		if refusal == nil && !found.timed() {
			refusal = refused("%s is not counted in minutes", found.text)
		}
		if refusal != nil {
			return resolvedKey{}, refusal
		}
		verb := verbComplete
		if kind == KindAdjustMinutes {
			verb = verbAdjust
		}
		return resolvedKey{verb: verb, found: found, minutes: &logged}, nil
	}
	found, refusal := d.listed(panel, key)
	switch {
	case refusal != nil:
		return resolvedKey{}, refusal
	case found.timed() && minutes == nil:
		return resolvedKey{verb: verbMinutes, found: found}, nil
	case found.timed() && *minutes < 1:
		return resolvedKey{}, refused("%s logs a whole number of minutes above zero", found.text)
	case !found.timed() && minutes != nil:
		return resolvedKey{}, refused("%s is not counted in minutes", found.text)
	}
	return resolvedKey{verb: verbComplete, found: found, minutes: minutes}, nil
}

func minutesPanel(panel Panel, found entry, minutes *int) Panel {
	panel.Step = StepMinutes
	panel.Minutes = &MinutesStep{Key: found.key, Text: found.text}
	panel.Rows = minutesRows(found, minutes)
	return panel
}

func (d *Desk) refuse(ctx context.Context, err *PickError) (Panel, error) {
	notify(ctx, "doit: not picked", err.Message)
	return Panel{}, err
}

// listed is the completion row that key names among the panel's parts now.
func (d *Desk) listed(panel *Panel, key string) (entry, *PickError) {
	found, ok := panel.find(key)
	if !ok {
		return entry{}, refused("the panel lists no row %s now", key)
	}
	if panel.held(key) {
		return entry{}, refused("%s is picked already, and is taken again once the watch reads the rows again", found.text)
	}
	if found.completes.Kind == KindHabit && !habitID.MatchString(found.completes.Ref) {
		return entry{}, refused("%s names habit %q, and a habit id is all digits", found.text, found.completes.Ref)
	}
	if setAction(found.actionOf()) && (found.completes.ItemID == nil || !habitID.MatchString(*found.completes.ItemID)) {
		return entry{}, refused("%s names no checklist item by an id of digits", found.text)
	}
	return found, nil
}

// complete runs a pick of one row in its turn. The state file marks the pick
// running before its command runs, and the pick ends with how it came out. A
// completion holds its key from a second pick until a read that begins after
// it ended, and one that recorded nothing frees it at once. A tick or an
// untick holds nothing, because each one records another set. The pick
// supersedes how the key's earlier picks were refused or skipped.
func (w *Watch) complete(ctx context.Context, found entry, minutes *int, notifyDone bool) (Panel, error) {
	d := w.desk
	before, leave, ok := w.turns.enter(commitGroup(found.completes, found.key), found.key)
	if !ok {
		return d.refuse(ctx, refused("%s is being picked already, so this pick does nothing", found.text))
	}
	defer leave()
	picked := d.Now()
	<-before
	moment := d.Now()
	pick := RunningPick{
		ID: service.NewWriteID(moment), Key: found.key, Text: found.text, Action: found.actionOf(), Completes: found.completes,
		Minutes: minutes, PickedAt: stamp(picked), StartedAt: stamp(moment),
	}
	readAt := d.Paths.panelReadAt()
	took := false
	err := d.Paths.change(func(state *State) error {
		took = !holdsKey(pick.Action) || state.takePick(readAt, pick.Key, moment)
		if took {
			state.forgetEnded(pick.Key)
			state.RunningPicks = append(state.RunningPicks, pick)
		}
		return nil
	})
	if err != nil {
		return d.refuse(ctx, &PickError{ExitCode: PickFailed, Message: "nothing was picked, because the desk nudge's state file could not be written: " + err.Error()})
	}
	if !took {
		return d.refuse(ctx, refused("%s is picked already, and is taken again once the watch reads the rows again", found.text))
	}
	outcome := w.commitRunning(ctx, pick, false, notifyDone, moment)
	if !outcome.ok {
		return Panel{}, &PickError{ExitCode: outcome.exitCode, Message: strings.TrimPrefix(outcome.summary, "doit: ") + ": " + outcome.body}
	}
	done := panelFor(d.Paths.readPanel(), d.Paths.readState(), StepDone)
	done.Message = &outcome.body
	return done, nil
}

// pickEnd is how one pick that ran is finished.
type pickEnd struct {
	key     string
	target  Completes
	action  string
	minutes *int
	// holds is whether the pick holds its key, which its outcome frees or keeps.
	holds bool
	// at is the moment its picked line carries.
	at   time.Time
	seen *snapshot
	// keep is what else the same write of the state file keeps.
	keep func(state *State)
	// unwritten says what a state file that could not be written leaves behind.
	unwritten  string
	notifyDone bool
}

// finishPick settles a pick that ran: it frees or keeps its key, ends a nudge
// in hand where the pick went through, notifies, and writes picked.
func (d *Desk) finishPick(ctx context.Context, end pickEnd, outcome completion) completion {
	var ended *Nudge
	err := d.Paths.change(func(state *State) error {
		if end.keep != nil {
			end.keep(state)
		}
		if end.holds {
			state.settlePick(end.key, d.Now(), outcome.outcome() == OutcomeRefused)
		}
		if outcome.ok && state.inHand() != nil {
			ended = state.endReady()
		}
		return nil
	})
	if err != nil {
		logger.Error("could not settle the pick of " + end.key + " in the state file: " + err.Error())
		outcome.body += ". The desk nudge's state file could not be written, so " + end.unwritten + ": " + err.Error()
	}
	if !outcome.ok || end.notifyDone || err != nil {
		notify(ctx, outcome.summary, outcome.body)
	}
	var nudgeID *string
	if ended != nil {
		nudgeID = &ended.NudgeID
	}
	d.Paths.appendRecords(pickedRecord(end.at, nudgeID, outcome.fields(end.target, end.action, end.minutes), end.seen))
	return outcome
}

// fields are what a picked line says of a pick that ran.
func (c completion) fields(target Completes, action string, minutes *int) pickedFields {
	var failure *string
	if !c.ok {
		failure = &c.body
	}
	return pickedFields{
		Kind: target.Kind, Ref: target.Ref, ItemID: target.ItemID, Minutes: minutes,
		Action: action, Outcome: c.outcome(), OK: c.ok, Error: failure,
	}
}

// lifeFailed is how a life habits call that failed ends. One that ran out of
// time may have landed, so it holds the row. Any other failure recorded nothing.
func lifeFailed(err error, summary string) completion {
	var failedRun *programs.Failed
	timedOut := errors.As(err, &failedRun) && failedRun.TimedOut
	return completion{summary: "doit: " + summary, body: err.Error(), exitCode: PickFailed, recordedNothing: !timedOut}
}

// completedHabit runs life habits complete on day, once a read of day's
// completions finds the habit not done there, because life accepts a second
// completion of a habit.
func (d *Desk) completedHabit(ctx context.Context, found entry, day string) completion {
	done, err := habitDoneOn(ctx, found.completes.Ref, day)
	if err != nil {
		return completion{
			summary: "doit: habit not completed", exitCode: PickFailed, recordedNothing: true,
			body: "life's completions could not be read again, so " + found.text + " was not completed: " + err.Error(),
		}
	}
	if done {
		said := "already done on " + day
		if day == d.Now().Format(dayLayout) {
			said = "already done today"
		}
		return completion{summary: "doit: " + found.text + " not completed", body: said, exitCode: PickRefused, skipped: true}
	}
	if _, err := habitProgram(found.completes.Ref, day).Run(ctx); err != nil {
		return lifeFailed(err, "habit not completed")
	}
	return completion{ok: true, summary: "doit: habit completed", body: found.text}
}

// setChanged runs life habits tick or untick on one checklist item.
func setChanged(ctx context.Context, found entry, changes programs.Program, done, failed string) completion {
	if _, err := changes.Run(ctx); err != nil {
		return lifeFailed(err, failed)
	}
	return completion{ok: true, summary: "doit: " + done, body: found.text}
}

// habitDoneOn reads life's completions of day again, and answers whether one is of the habit.
func habitDoneOn(ctx context.Context, habit, day string) (bool, error) {
	out, err := habitsCompletedProgram(day).Run(ctx)
	if err != nil {
		return false, err
	}
	var completions []struct {
		HabitID      json.Number `json:"habit_id"`
		CompleteDate string      `json:"complete_date"`
	}
	if err := json.Unmarshal([]byte(out), &completions); err != nil {
		return false, fmt.Errorf("life habits completed --json printed no list of completions: %w", err)
	}
	for _, done := range completions {
		if done.HabitID.String() == habit && done.CompleteDate == day {
			return true, nil
		}
	}
	return false, nil
}

// markedReviewDone records a review register's entry done on day, the desk's
// own day when it was picked. The service keeps one completion a day for an
// entry, so a failure frees the row and a resend records nothing twice.
func (d *Desk) markedReviewDone(ctx context.Context, ref, day string) completion {
	ctx, cancel := context.WithTimeout(ctx, writeBound)
	defer cancel()
	client, err := d.Open(ctx)
	var done service.ReviewDone
	if err == nil {
		done, err = client.CompleteReview(ctx, ref, day)
	}
	if err != nil {
		return completion{summary: "doit: " + ref + " not marked done", body: err.Error(), exitCode: service.ExitCode(err), recordedNothing: true}
	}
	body := fmt.Sprintf("%s (%s)", ref, done.DoneOn)
	if again := done.DueAgain(); again != "" {
		body += " · due again in " + again
	}
	return completion{ok: true, summary: "doit: marked done", body: body}
}

// logWrite is how one pursuit log is written. Its id names the journal entry,
// so the service stores a resend once. save keeps its progress on the way, so a
// restart knows how far it got.
type logWrite struct {
	id string
	// save keeps the entry as on_log begins, saying it may not have run, and
	// again once on_log has ended. An error at the first save stops the log
	// before on_log runs.
	save func(body map[string]any) error
}

func pursuitNotLogged(name string, err error) completion {
	var none *service.NoAnswer
	return completion{
		summary: "doit: " + name + " not logged", body: err.Error(), exitCode: service.ExitCode(err),
		recordedNothing: !errors.As(err, &none),
	}
}

// loggedPursuit logs a pursuit, naming the row's choice, after its on_log runs
// unasked, because the pick is the asking.
//
// It logs as `doit pursuits log` does. Every log held on this desk is sent
// first, and where one of them was this pursuit's, the pick is that log.
// Otherwise the service's check names the choice: the one the row names, or
// with none named the only one the resolve offers. on_log runs against that
// choice, then the log posts what on_log did.
func (d *Desk) loggedPursuit(ctx context.Context, target Completes, minutes *int, journal logWrite) completion {
	name := target.Ref
	ctx, cancel := context.WithTimeout(ctx, pursuits.OnLogBound+2*writeBound)
	defer cancel()
	client, err := d.Open(ctx)
	if err != nil {
		return pursuitNotLogged(name, err)
	}
	held := pursuits.HeldLogs{Dir: d.Paths.HeldLogs}
	sent, err := held.SendHeld(ctx, client)
	if err != nil {
		return pursuitNotLogged(name, err)
	}
	check, err := client.CheckLog(ctx, service.LogAsked{Pursuit: name, Minutes: minutes, ItemID: target.ItemID})
	if err != nil {
		return pursuitNotLogged(name, err)
	}
	for _, each := range sent {
		if each.Refused != nil {
			logger.Warn("the held log of " + fmt.Sprint(each.Body["pursuit"]) + " was refused: " + each.Refused.Error())
		} else if each.Body["pursuit"] == check.Pursuit {
			return completion{ok: true, summary: "doit: logged", body: "Sent the held log of " + pursuits.Line(each.Body, each.Logged)}
		}
	}
	run := pursuits.Run{
		Check: check, Chosen: check.Chosen, Held: held,
		Entry: pursuits.Entry{ID: journal.id, Minutes: minutes, Machine: d.Machine, Actor: d.Actor},
	}
	if journal.save != nil {
		run.Started = journal.save
		run.Ended = func(body map[string]any) {
			if err := journal.save(body); err != nil {
				logger.Warn("could not save " + name + "'s entry before posting it, so a restart sends the one saved as on_log began: " + err.Error())
			}
		}
	}
	body, logged, err := pursuits.Log(ctx, client, run)
	return loggedOutcome(name, body, logged, err)
}

// postedLog posts a pursuit's journal entry that a restart found saved, and
// says what it logged. The service keeps an entry by its id, so a resend is
// stored once. An entry it cannot post is held on this desk.
func (d *Desk) postedLog(ctx context.Context, body map[string]any) completion {
	name := fmt.Sprint(body["pursuit"])
	ctx, cancel := context.WithTimeout(ctx, writeBound)
	defer cancel()
	held := pursuits.HeldLogs{Dir: d.Paths.HeldLogs}
	client, err := d.Open(ctx)
	if err != nil {
		if path, holdErr := held.Hold(body); holdErr == nil && service.LaterSendCanStore(err) {
			err = &pursuits.HeldError{Err: err, Path: path}
		}
		return loggedOutcome(name, body, service.Logged{}, err)
	}
	logged, err := held.Post(ctx, client, body)
	return loggedOutcome(name, body, logged, err)
}

// loggedOutcome says how a pursuit's log ended.
func loggedOutcome(name string, body map[string]any, logged service.Logged, err error) completion {
	downstream, _ := body["downstream"].(map[string]any)
	var held *pursuits.HeldError
	switch {
	case errors.As(err, &held):
		return completion{
			summary: "doit: " + name + " held, not yet recorded", exitCode: service.ExitCode(err),
			body: err.Error() + "\nThe log is held at " + held.Path + ", and the next log of a pursuit sends it again.",
		}
	case err != nil && pursuits.Acted(downstream):
		return completion{
			summary: "doit: " + name + " not recorded", exitCode: service.ExitCode(err),
			body: strings.Join(append([]string{err.Error()}, pursuits.RepairLines(body)...), "\n"),
		}
	case err != nil:
		return pursuitNotLogged(name, err)
	}
	line := "Logged " + pursuits.Line(body, logged)
	if !pursuits.Acted(downstream) {
		return completion{ok: true, summary: "doit: logged", body: line}
	}
	command := fmt.Sprint(downstream["command"])
	if failure := pursuits.Failure(downstream); failure != "" {
		return completion{
			summary: "doit: " + name + " logged, and its on_log failed", exitCode: PickFailed,
			body: fmt.Sprintf("%s\n%s %s", line, command, failure),
		}
	}
	return completion{ok: true, summary: "doit: logged", body: line + "\nRan " + command}
}

// pickedAnswer answers through Answer, which notifies a failure, and fails as
// `doit desk-nudge answer` fails for it.
func (d *Desk) pickedAnswer(ctx context.Context, answer, nudgeID string) (Panel, error) {
	answered, err := d.Answer(ctx, answer, nudgeID)
	if errors.Is(err, ErrNothingToAnswer) {
		return d.refuse(ctx, refused("%s", strings.TrimPrefix(err.Error(), ErrNothingToAnswer.Error()+": ")))
	}
	if err != nil {
		return Panel{}, err
	}
	code, said := answered.Outcome()
	if code != 0 {
		return Panel{}, &PickError{ExitCode: code, Message: "answer not saved: " + said}
	}
	done := panelFor(d.Paths.readPanel(), d.Paths.readState(), StepDone)
	done.Message = &said
	return done, nil
}
