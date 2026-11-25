package desknudge

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

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
		ID: newID(moment), Key: found.key, Text: found.text, Action: found.actionOf(), Completes: found.completes,
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
	var failedRun *programFailed
	timedOut := errors.As(err, &failedRun) && failedRun.timedOut
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
	if _, err := habitProgram(found.completes.Ref, day).run(ctx); err != nil {
		return lifeFailed(err, "habit not completed")
	}
	return completion{ok: true, summary: "doit: habit completed", body: found.text}
}

// setChanged runs life habits tick or untick on one checklist item.
func setChanged(ctx context.Context, found entry, changes program, done, failed string) completion {
	if _, err := changes.run(ctx); err != nil {
		return lifeFailed(err, failed)
	}
	return completion{ok: true, summary: "doit: " + done, body: found.text}
}

// habitDoneOn reads life's completions of day again, and answers whether one is of the habit.
func habitDoneOn(ctx context.Context, habit, day string) (bool, error) {
	out, err := habitsCompletedProgram(day).run(ctx)
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
	var done struct {
		DoneOn string `json:"done_on"`
		Status *struct {
			CadenceDays *float64 `json:"cadence_days"`
		} `json:"status"`
	}
	if err := d.post(ctx, "/review/"+url.PathEscape(ref)+"/completions", map[string]string{"day": day}, &done); err != nil {
		return completion{summary: "doit: " + ref + " not marked done", body: err.Error(), exitCode: service.ExitCode(err), recordedNothing: true}
	}
	body := fmt.Sprintf("%s (%s)", ref, done.DoneOn)
	if done.Status != nil && done.Status.CadenceDays != nil && *done.Status.CadenceDays != 0 {
		body += " · due again in " + formatMinutes(*done.Status.CadenceDays) + "d"
	}
	return completion{ok: true, summary: "doit: marked done", body: body}
}

// resolvedItem is a pursuit's resolve, as /pursuits/{name}/item answers it. It
// is empty where nothing resolved, and an error entry carries no choices.
type resolvedItem struct {
	Pursuit    *string          `json:"pursuit,omitempty"`
	Backend    any              `json:"backend,omitempty"`
	Candidates *float64         `json:"candidates,omitempty"`
	Choices    []map[string]any `json:"choices,omitempty"`
	Error      any              `json:"error,omitempty"`
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
// It follows `doit pursuits log`: the choice is the one the row names, or with
// none named the only one the resolve offers. on_log runs against that choice,
// then the log posts what on_log did. The log names the choice where the row
// named it, the resolve counted one candidate, or on_log completed it.
func (d *Desk) loggedPursuit(ctx context.Context, target Completes, minutes *int, journal logWrite) completion {
	name := target.Ref
	notLogged := func(err error) completion { return pursuitNotLogged(name, err) }
	var register struct {
		Pursuits map[string]struct {
			Resolve any    `json:"resolve,omitempty"`
			OnLog   string `json:"on_log,omitempty"`
		} `json:"pursuits"`
	}
	if err := d.get(ctx, "/pursuits/register", &register); err != nil {
		return notLogged(err)
	}
	config, declared := register.Pursuits[name]
	if !declared {
		return notLogged(fmt.Errorf("no pursuit named %s", name))
	}
	var item resolvedItem
	if config.Resolve != nil {
		var answered struct {
			Resolved resolvedItem `json:"resolved"`
		}
		if err := d.get(ctx, "/pursuits/"+url.PathEscape(name)+"/item", &answered); err != nil {
			return notLogged(err)
		}
		item = answered.Resolved
	}
	chosen, refusal := chosenChoice(name, item, target.ItemID)
	if refusal != nil {
		return notLogged(refusal)
	}
	now := d.Now()
	entryOf := func(downstream map[string]any) map[string]any {
		acted := downstream != nil && downstream["ran"] == true
		var named map[string]any
		if chosen != nil && (target.ItemID != nil || item.Candidates == nil || *item.Candidates <= 1 || acted) {
			named = chosen
		}
		return map[string]any{
			"id": journal.id, "pursuit": name, "occurred_at": stamp(now), "note": nil, "duration_minutes": minutes,
			"item": named, "downstream": downstream, "machine": d.Machine, "actor": d.Actor,
		}
	}
	var starts func(string) error
	if journal.save != nil {
		starts = func(command string) error {
			return journal.save(entryOf(map[string]any{"command": command, "ran": nil, "exit_code": nil, "error": "the watch stopped while on_log ran"}))
		}
	}
	downstream, ranErr := d.ranOnLog(ctx, config.OnLog, chosen, minutes, starts)
	if ranErr != nil {
		return notLogged(ranErr)
	}
	body := entryOf(downstream)
	if journal.save != nil && downstream != nil {
		if err := journal.save(body); err != nil {
			logger.Warn("could not save " + name + "'s entry before posting it, so a restart sends the one saved as on_log began: " + err.Error())
		}
	}
	return d.postedLog(ctx, body)
}

// postedLog posts a pursuit's journal entry, and says what it logged. A resend
// of one entry is stored once, because the service keeps an entry by its id.
func (d *Desk) postedLog(ctx context.Context, body map[string]any) completion {
	name := fmt.Sprint(body["pursuit"])
	minutes, _ := wholeNumber(body["duration_minutes"])
	downstream, _ := body["downstream"].(map[string]any)
	named, _ := body["item"].(map[string]any)
	acted := downstream != nil && downstream["ran"] == true
	var logged struct {
		DueInDays *float64 `json:"due_in_days"`
	}
	if err := d.post(ctx, "/journal/done", body, &logged); err != nil {
		if !acted {
			return pursuitNotLogged(name, err)
		}
		repair := "doit pursuits log " + name + " --no-write"
		if minutes != nil {
			repair += " --minutes " + strconv.Itoa(*minutes)
		}
		occurred, _ := body["occurred_at"].(string)
		at, _ := instant(&occurred)
		return completion{summary: "doit: " + name + " not recorded", exitCode: service.ExitCode(err), body: strings.Join([]string{
			err.Error(),
			"on_log already ran: " + fmt.Sprint(downstream["command"]),
			"Record it without running on_log again, adding --ago for the time since " + at.Format("15:04") + ":  " + repair,
		}, "\n")}
	}
	line := "Logged " + name
	if label, _ := named["label"].(string); label != "" {
		line += " — " + label
	}
	if logged.DueInDays != nil {
		line += " · " + formatDue(*logged.DueInDays)
	}
	if !acted {
		return completion{ok: true, summary: "doit: logged", body: line}
	}
	command := fmt.Sprint(downstream["command"])
	if code, exited := wholeNumber(downstream["exit_code"]); !exited || *code != 0 {
		return completion{
			summary: "doit: " + name + " logged, and its on_log failed", exitCode: PickFailed,
			body: fmt.Sprintf("%s\n%s %s", line, command, onLogFailure(downstream)),
		}
	}
	return completion{ok: true, summary: "doit: logged", body: line + "\nRan " + command}
}

// wholeNumber reads a whole number from an entry, which holds an int as built
// and a float64 once read back from the state file.
func wholeNumber(value any) (*int, bool) {
	switch number := value.(type) {
	case int:
		return &number, true
	case *int:
		return number, number != nil
	case float64:
		whole := int(number)
		return &whole, true
	}
	return nil, false
}

func onLogFailure(downstream map[string]any) string {
	if code, ok := wholeNumber(downstream["exit_code"]); ok {
		return fmt.Sprintf("exited %d", *code)
	}
	return fmt.Sprint(downstream["error"])
}

// chosenChoice is the choice a log is about, flattened into the row a log
// records: the one carrying itemID, or with none named the only one offered.
// Nil where the log is about none of them.
func chosenChoice(name string, item resolvedItem, itemID *string) (map[string]any, error) {
	index := -1
	switch {
	case itemID != nil:
		if item.Error != nil {
			return nil, fmt.Errorf("%s could not be read, so item %s cannot be checked: %v", name, *itemID, item.Error)
		}
		var held []string
		for i, choice := range item.Choices {
			id := fmt.Sprint(choice["id"])
			if choice["id"] != nil && id == *itemID {
				index = i
			}
			if choice["id"] != nil {
				held = append(held, fmt.Sprintf("%s (%v)", id, choice["label"]))
			}
		}
		if index < 0 {
			return nil, fmt.Errorf("%s holds no item %s now. Its items now: %s", name, *itemID, strings.Join(held, ", "))
		}
	case len(item.Choices) == 1:
		index = 0
	}
	if index < 0 {
		return nil, nil
	}
	chosen := map[string]any{"pursuit": item.Pursuit, "backend": item.Backend, "candidates": item.Candidates}
	for field, value := range item.Choices[index] {
		chosen[field] = value
	}
	return chosen, nil
}

// ranOnLog runs a pursuit's on_log against the chosen item, and answers what it did, or nil where nothing ran.
// A program that could not start ran nothing, so the pick is refused before anything is logged.
// starts, where given, saves the command before it runs.
func (d *Desk) ranOnLog(ctx context.Context, template string, chosen map[string]any, minutes *int, starts func(string) error) (map[string]any, error) {
	if template == "" || chosen == nil {
		return nil, nil
	}
	text := func(field string) string {
		if value, ok := chosen[field]; ok && value != nil {
			return fmt.Sprint(value)
		}
		return ""
	}
	if strings.Contains(template, "{id}") && text("id") == "" {
		return nil, nil
	}
	ran, err := onLogProgram(template, map[string]string{
		"id": text("id"), "label": text("label"), "note": "", "minutes": itoa(minutes), "pursuit": text("pursuit"),
	})
	if err != nil {
		return nil, err
	}
	command := shellJoin(ran.argv)
	if starts != nil {
		if err := starts(command); err != nil {
			return nil, err
		}
	}
	_, runErr := ran.run(ctx)
	var failedRun *programFailed
	switch {
	case runErr == nil:
		return map[string]any{"command": command, "ran": true, "exit_code": 0}, nil
	case errors.As(runErr, &failedRun) && failedRun.timedOut:
		return map[string]any{"command": command, "ran": true, "exit_code": nil, "error": runErr.Error()}, nil
	case errors.As(runErr, &failedRun) && failedRun.exited:
		return map[string]any{"command": command, "ran": true, "exit_code": failedRun.exitCode, "error": runErr.Error()}, nil
	}
	return nil, runErr
}

// shellJoin quotes each argument that a shell would split, so the command reads as it ran.
func shellJoin(argv []string) string {
	quoted := make([]string, len(argv))
	for i, word := range argv {
		if word != "" && !strings.ContainsAny(word, " \t\n'\"\\$`!*?[]{}()<>|&;#~") {
			quoted[i] = word
			continue
		}
		quoted[i] = "'" + strings.ReplaceAll(word, "'", `'"'"'`) + "'"
	}
	return strings.Join(quoted, " ")
}

// formatDue is when a pursuit is next due, in words that need no legend.
func formatDue(days float64) string {
	switch {
	case days <= -1:
		return spanText(days) + " overdue"
	case days < 1:
		return "due today"
	}
	return "due in " + spanText(days)
}

// spanText is a number of days in the coarsest unit that still says something.
func spanText(days float64) string {
	days = math.Abs(days)
	switch {
	case days < 14:
		return fmt.Sprintf("%dd", max(int(math.RoundToEven(days)), 1))
	case days < 90:
		return fmt.Sprintf("%dw", max(int(math.RoundToEven(days/7)), 1))
	}
	return fmt.Sprintf("%dmo", max(int(math.RoundToEven(days/30)), 1))
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
