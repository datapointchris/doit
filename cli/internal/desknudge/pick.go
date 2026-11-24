package desknudge

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"

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
}

var habitID = regexp.MustCompile(`^[0-9]+$`)

// Pick acts on one panel row by its key, and answers the next step: a done
// step once something is completed, or a timed pursuit's minutes. minutes
// logs a timed pursuit at once. read is called once a completion went
// through, so the parts are read again.
//
// The watch owns the vocabulary of keys. An answer key and a minutes key are
// read as the watch wrote them, and any other key is resolved against the
// panel's parts, whose completes says what to run.
func (d *Desk) Pick(ctx context.Context, key string, minutes *int, read func()) (Panel, error) {
	kind, rest, _ := strings.Cut(key, keySeparator)
	if kind == KindAnswer {
		answer, nudgeID, _ := strings.Cut(rest, keySeparator)
		if !IsAnswer(answer) {
			return d.refuse(ctx, refused("%q is not an answer", answer))
		}
		return d.pickedAnswer(ctx, answer, nudgeID)
	}
	cached := d.Paths.readPanel()
	panel := panelFor(cached, d.Paths.readState(), StepToday)
	if kind == KindLogMinutes || kind == KindAdjustMinutes {
		amount, rowKey, _ := strings.Cut(rest, keySeparator)
		logged, err := strconv.Atoi(amount)
		if err != nil || logged < 1 {
			return d.refuse(ctx, refused("%s names no whole number of minutes", key))
		}
		found, pickErr := d.listed(&panel, rowKey)
		if pickErr == nil && !found.timed() {
			pickErr = refused("%s is not counted in minutes", found.text)
		}
		if pickErr != nil {
			return d.refuse(ctx, pickErr)
		}
		if kind == KindAdjustMinutes {
			return minutesPanel(panel, found, &logged), nil
		}
		return d.complete(ctx, panel, found, &logged, read)
	}
	found, err := d.listed(&panel, key)
	if err != nil {
		return d.refuse(ctx, err)
	}
	switch {
	case found.timed() && minutes == nil:
		return minutesPanel(panel, found, nil), nil
	case !found.timed() && minutes != nil:
		return d.refuse(ctx, refused("%s is not counted in minutes", found.text))
	}
	return d.complete(ctx, panel, found, minutes, read)
}

func minutesPanel(panel Panel, found entry, minutes *int) Panel {
	panel.Step = StepMinutes
	step := minutesRows(found, minutes)
	panel.Minutes = &step
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
	if found.completes.Kind == "habit" && !habitID.MatchString(found.completes.Ref) {
		return entry{}, refused("%s names habit %q, and a habit id is all digits", found.text, found.completes.Ref)
	}
	return found, nil
}

// complete holds the pick, completes the row, then settles the pick, ends a
// nudge in hand where it went through, and writes picked. A completion that
// recorded nothing frees its row at once. Every other one holds it until a
// read that begins after it ended, which read starts.
func (d *Desk) complete(ctx context.Context, panel Panel, found entry, minutes *int, read func()) (Panel, error) {
	moment := d.Now()
	took := false
	err := d.Paths.change(func(state *State) error {
		took = state.takePick(panel.ReadAt, found.key, moment)
		return nil
	})
	if err != nil {
		return Panel{}, err
	}
	if !took {
		return d.refuse(ctx, refused("%s is picked already, and is taken again once the watch reads the rows again", found.text))
	}
	seen := takeSnapshot(ctx)
	outcome := d.completed(ctx, found, minutes)
	var ended *Nudge
	_ = d.Paths.change(func(state *State) error {
		state.settlePick(found.key, d.Now(), outcome.recordedNothing)
		if outcome.ok && state.inHand() != nil {
			ended = state.endReady()
		}
		return nil
	})
	notify(ctx, outcome.summary, outcome.body)
	var nudgeID, failure *string
	if ended != nil {
		nudgeID = &ended.NudgeID
	}
	if !outcome.ok {
		failure = &outcome.body
	}
	d.Paths.appendRecords(pickedRecord(moment, nudgeID, found.completes, minutes, failure, seen))
	if read != nil {
		read()
	}
	if !outcome.ok {
		return Panel{}, &PickError{ExitCode: outcome.exitCode, Message: strings.TrimPrefix(outcome.summary, "doit: ") + ": " + outcome.body}
	}
	done := panelFor(d.Paths.readPanel(), d.Paths.readState(), StepDone)
	done.Message = &outcome.body
	return done, nil
}

// completed completes one row through its owner, with what doit builds from
// the row's completes.
func (d *Desk) completed(ctx context.Context, found entry, minutes *int) completion {
	switch found.completes.Kind {
	case "pursuit":
		return d.loggedPursuit(ctx, found, minutes)
	case "habit":
		return completedHabit(ctx, found)
	case "review", "practice":
		return d.markedReviewDone(ctx, found)
	}
	return completion{
		summary: "doit: not completed", body: fmt.Sprintf("this doit completes no %s row", found.completes.Kind),
		exitCode: PickFailed, recordedNothing: true,
	}
}

// completedHabit runs life habits complete. One that ran out of time may have landed, so it holds the row.
func completedHabit(ctx context.Context, found entry) completion {
	if _, err := habitProgram(found.completes.Ref).run(ctx); err != nil {
		var failedRun *programFailed
		timedOut := errors.As(err, &failedRun) && failedRun.timedOut
		return completion{summary: "doit: habit not completed", body: err.Error(), exitCode: PickFailed, recordedNothing: !timedOut}
	}
	return completion{ok: true, summary: "doit: habit completed", body: found.text}
}

// markedReviewDone records a review or practice entry done today, on the
// desk's own day. The service keeps one completion a day for an entry, so a
// failure frees the row.
func (d *Desk) markedReviewDone(ctx context.Context, found entry) completion {
	ref := found.completes.Ref
	var done struct {
		DoneOn string `json:"done_on"`
		Status *struct {
			CadenceDays *float64 `json:"cadence_days"`
		} `json:"status"`
	}
	day := d.Now().Format("2006-01-02")
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

// loggedPursuit logs a pursuit, naming the row's choice, after its on_log runs
// unasked, because the pick is the asking.
//
// It follows `doit pursuits log`: the choice is the one the row names, or with
// none named the only one the resolve offers. on_log runs against that choice,
// then the log posts what on_log did. The log names the choice where the row
// named it, the resolve counted one candidate, or on_log completed it.
func (d *Desk) loggedPursuit(ctx context.Context, found entry, minutes *int) completion {
	name := found.completes.Ref
	notLogged := func(err error) completion {
		var none *service.NoAnswer
		return completion{
			summary: "doit: " + name + " not logged", body: err.Error(), exitCode: service.ExitCode(err),
			recordedNothing: !errors.As(err, &none),
		}
	}
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
	chosen, refusal := chosenChoice(name, item, found.completes.ItemID)
	if refusal != nil {
		return notLogged(refusal)
	}
	downstream, ranErr := d.ranOnLog(ctx, config.OnLog, chosen, minutes)
	if ranErr != nil {
		return notLogged(ranErr)
	}
	acted := downstream != nil && downstream["ran"] == true
	var named map[string]any
	if chosen != nil && (found.completes.ItemID != nil || item.Candidates == nil || *item.Candidates <= 1 || acted) {
		named = chosen
	}
	now := d.Now()
	body := map[string]any{
		"id": newID(now), "pursuit": name, "occurred_at": stamp(now), "note": nil, "duration_minutes": minutes,
		"item": named, "downstream": downstream, "machine": d.Machine, "actor": d.Actor,
	}
	var logged struct {
		DueInDays *float64 `json:"due_in_days"`
	}
	if err := d.post(ctx, "/journal/done", body, &logged); err != nil {
		if !acted {
			return notLogged(err)
		}
		repair := "doit pursuits log " + name + " --no-write"
		if minutes != nil {
			repair += " --minutes " + strconv.Itoa(*minutes)
		}
		return completion{summary: "doit: " + name + " not recorded", exitCode: service.ExitCode(err), body: strings.Join([]string{
			err.Error(),
			"on_log already ran: " + fmt.Sprint(downstream["command"]),
			"Record it without running on_log again, adding --ago for the time since " + now.Format("15:04") + ":  " + repair,
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
	if code, exited := downstream["exit_code"].(int); !exited || code != 0 {
		return completion{
			summary: "doit: " + name + " logged, and its on_log failed", exitCode: PickFailed,
			body: fmt.Sprintf("%s\n%s %s", line, command, onLogFailure(downstream)),
		}
	}
	return completion{ok: true, summary: "doit: logged", body: line + "\nRan " + command}
}

func onLogFailure(downstream map[string]any) string {
	if code, ok := downstream["exit_code"].(int); ok {
		return fmt.Sprintf("exited %d", code)
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
func (d *Desk) ranOnLog(ctx context.Context, template string, chosen map[string]any, minutes *int) (map[string]any, error) {
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
