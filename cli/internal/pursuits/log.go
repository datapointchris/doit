// Package pursuits logs a pursuit as the service checks it: the choice that
// the log is about, its on_log run on this desk, and the journal entry that
// records both. `doit pursuits log` and the desk nudge watch's pick each log
// through it, so the service's rules reach both the same way.
package pursuits

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/datapointchris/doit/cli/internal/programs"
	"github.com/datapointchris/doit/cli/internal/service"
)

// Run is one log of a pursuit once the service's check has passed. A caller
// asks the person what the check leaves open, and fills in the rest.
type Run struct {
	Check service.LogCheck
	// Chosen is what the log is about: the check's choice, or the person's pick.
	Chosen map[string]any
	// Entry is the log's fields. Its instant is the one the check fixed.
	Entry Entry
	// Approve says whether on_log runs and, where it does not, what the log
	// records instead. A nil Approve runs it unasked.
	Approve func(*OnLog) (run bool, instead map[string]any)
	// Started keeps the entry as on_log begins, saying it may not have run.
	// An error stops the log before on_log runs.
	Started func(body map[string]any) error
	// Ended keeps the entry once on_log has ended.
	Ended func(body map[string]any)
	// Held keeps the entry on this desk until the service answers it.
	Held HeldLogs
}

// Log runs the pursuit's on_log where Approve agrees, then posts the entry
// that records what it did. It answers the body it posted. Where no answer
// says the entry was stored, it stays held, and the error is a *HeldError.
func Log(ctx context.Context, client *service.Client, run Run) (map[string]any, service.Logged, error) {
	occurred, err := time.Parse(time.RFC3339Nano, run.Check.OccurredAt)
	if err != nil {
		return nil, service.Logged{}, fmt.Errorf("the service's check answered occurred_at %q, which is not an instant", run.Check.OccurredAt)
	}
	run.Entry.Pursuit, run.Entry.Occurred = run.Check.Pursuit, occurred
	onLog, err := OnLogFor(run.Check.OnLog, run.Chosen, run.Entry.Note, run.Entry.Minutes)
	if err != nil {
		return nil, service.Logged{}, err
	}
	var downstream map[string]any
	if onLog != nil {
		approved, instead := true, map[string]any(nil)
		if run.Approve != nil {
			approved, instead = run.Approve(onLog)
		}
		downstream = instead
		if approved {
			if run.Started != nil {
				started := map[string]any{"command": onLog.Command, "ran": nil, "exit_code": nil, "error": "stopped while on_log ran"}
				if err := run.Started(run.Entry.Body(run.Check.NamesChosen, run.Chosen, started)); err != nil {
					return nil, service.Logged{}, err
				}
			}
			if downstream, err = onLog.Ran(ctx); err != nil {
				return nil, service.Logged{}, err
			}
		}
	}
	body := run.Entry.Body(run.Check.NamesChosen, run.Chosen, downstream)
	if run.Ended != nil && downstream != nil {
		run.Ended(body)
	}
	logged, err := run.Held.Post(ctx, client, body)
	return body, logged, err
}

// Entry is one log of a pursuit, as this desk writes it.
type Entry struct {
	// ID keys the entry, so the service stores a resend once.
	ID       string
	Pursuit  string
	Occurred time.Time
	Note     string
	Minutes  *int
	Machine  string
	Actor    string
}

// Body is the entry as POST /journal/done takes it. It names chosen where the
// check says the log names its choice, or where on_log acted on it, because
// otherwise the log says the pursuit happened and not which of its choices.
func (e Entry) Body(namesChosen bool, chosen, downstream map[string]any) map[string]any {
	var named map[string]any
	if chosen != nil && (namesChosen || Acted(downstream)) {
		named = chosen
	}
	var note any
	if e.Note != "" {
		note = e.Note
	}
	return map[string]any{
		"id": e.ID, "pursuit": e.Pursuit, "occurred_at": e.Occurred.Format(instantLayout), "note": note,
		"duration_minutes": e.Minutes, "item": named, "downstream": downstream, "machine": e.Machine, "actor": e.Actor,
	}
}

// instantLayout is an instant as the service reads one, with the desk's offset.
const instantLayout = "2006-01-02T15:04:05.000-07:00"

// Acted is whether on_log ran, as the log's downstream records it.
func Acted(downstream map[string]any) bool { return downstream != nil && downstream["ran"] == true }

// Line is what a log says once it lands: the pursuit, the choice it names, and when it next comes due.
func Line(body map[string]any, logged service.Logged) string {
	line := fmt.Sprint(body["pursuit"])
	named, _ := body["item"].(map[string]any)
	if label, _ := named["label"].(string); label != "" {
		line += " — " + label
	}
	if logged.Due != nil {
		line += " · " + *logged.Due
	}
	return line
}

// RepairLines say what on_log already did, and the log that records the entry
// without running on_log again, for an entry the service never took.
func RepairLines(body map[string]any) []string {
	downstream, _ := body["downstream"].(map[string]any)
	repair := []string{"doit", "pursuits", "log", fmt.Sprint(body["pursuit"]), "--no-write"}
	if minutes, ok := WholeNumber(body["duration_minutes"]); ok {
		repair = append(repair, "--minutes", strconv.Itoa(*minutes))
	}
	if note, _ := body["note"].(string); note != "" {
		repair = append(repair, note)
	}
	occurred, _ := body["occurred_at"].(string)
	at, err := time.Parse(instantLayout, occurred)
	since := occurred
	if err == nil {
		since = at.Format("15:04")
	}
	return []string{
		"on_log already ran: " + fmt.Sprint(downstream["command"]),
		"Record it without running on_log again, adding --ago for the time since " + since + ":  " + programs.ShellJoin(repair),
	}
}

// WholeNumber reads a whole number from an entry, which holds an int as built
// and a float64 once read back from JSON.
func WholeNumber(value any) (*int, bool) {
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
