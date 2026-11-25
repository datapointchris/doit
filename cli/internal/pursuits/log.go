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

// Check is the log route's answer to a check, which writes nothing.
type Check struct {
	// Pursuit is the one pursuit that the typed name matched.
	Pursuit string `json:"pursuit"`
	// OnLog is the template that completes the choice in its app, or nil.
	OnLog *string `json:"on_log"`
	// MinutesRefusal is why the log still needs minutes, where it does.
	MinutesRefusal *string `json:"minutes_refusal"`
	// Choices are the pursuit's current choices, each with its line and the item a log records.
	Choices []Choice `json:"choices"`
	// Chosen is the choice that the log is about, flattened as a log records it, or nil.
	Chosen map[string]any `json:"chosen"`
	// Ask is several choices, none named, where only the person logging can say which.
	Ask bool `json:"ask"`
	// NamesChosen is whether the log names its choice whatever on_log does.
	NamesChosen bool `json:"names_chosen"`
}

// Choice is one of a pursuit's current choices.
type Choice struct {
	Line string         `json:"line"`
	Item map[string]any `json:"item"`
}

// Checked asks the log route whether a log of name would be taken, writing
// nothing. A name that means no one pursuit is refused with 404, minutes or
// an item that do not fit with 422, and an item of an app that could not be
// read with 409.
func Checked(ctx context.Context, client *service.Client, name string, minutes *int, itemID *string) (Check, error) {
	body := map[string]any{"pursuit": name, "check": true, "duration_minutes": minutes}
	if itemID != nil {
		body["item_id"] = *itemID
	}
	var check Check
	err := client.Post(ctx, "/journal/done", body, true, &check)
	return check, err
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

// Logged is the log route's answer once it stores an entry.
type Logged struct {
	DueInDays *float64 `json:"due_in_days"`
	// Due is when the pursuit next comes due, as a person reads it, or nil without a schedule.
	Due *string `json:"due"`
}

// Posted sends body to the log route. The service keeps an entry by its id, so a resend is stored once.
func Posted(ctx context.Context, client *service.Client, body map[string]any) (Logged, error) {
	var logged Logged
	err := client.Post(ctx, "/journal/done", body, true, &logged)
	return logged, err
}

// Line is what a log says once it lands: the pursuit, the choice it names, and when it next comes due.
func Line(body map[string]any, logged Logged) string {
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
