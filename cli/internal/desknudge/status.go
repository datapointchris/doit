package desknudge

import (
	"fmt"
	"io"
	"strings"
	"time"
)

// LabelOf is an answer's words, as the view last read named them.
func (d *Desk) LabelOf(answer string) string { return d.labelOf(answer) }

// StatusSchemaVersion is the shape of `doit desk-nudge status --json`, which
// schemas/desk-nudge-status.schema.json describes.
const StatusSchemaVersion = 3

// Status is where the clock stands, read from this desk's state file with no request.
type Status struct {
	SchemaVersion     int            `json:"schema_version"`
	Phase             Phase          `json:"phase"`
	WatchRunning      bool           `json:"watch_running"`
	CountedMinutes    float64        `json:"counted_minutes"`
	IntervalMinutes   float64        `json:"interval_minutes"`
	MinutesLeft       *int           `json:"minutes_left"`
	NudgeID           *string        `json:"nudge_id"`
	FiredAt           *string        `json:"fired_at"`
	ReadyUntil        *string        `json:"ready_until"`
	Suggestion        *Suggestion    `json:"suggestion"`
	AnswerCounts      map[string]int `json:"answer_counts"`
	QueuedAnswerCount int            `json:"queued_answer_count"`
	Pill              Pill           `json:"pill"`
	// PendingPicks are the clicks waiting for their commit_at, or committing.
	PendingPicks []ShownPick `json:"pending_picks"`
}

// Status reads the clock from the state file, under no lock and with no request.
func (d *Desk) Status() Status {
	return statusOf(d.Paths.readState(), d.Now())
}

func statusOf(state *State, now time.Time) Status {
	status := Status{
		SchemaVersion:     StatusSchemaVersion,
		Phase:             state.Phase,
		CountedMinutes:    rounded(state.CountedSeconds/60, 2),
		IntervalMinutes:   state.IntervalMinutes,
		AnswerCounts:      state.AnswerCounts,
		QueuedAnswerCount: len(state.QueuedAnswers),
		Pill:              state.Pill,
		PendingPicks:      []ShownPick{},
	}
	for _, pick := range state.PendingPicks {
		status.PendingPicks = append(status.PendingPicks, pick.shown())
	}
	if stale, ok := instant(state.Pill.StaleAfter); ok {
		status.WatchRunning = now.Before(stale)
	}
	if nudge := state.inHand(); nudge != nil {
		status.NudgeID, status.FiredAt = &nudge.NudgeID, &nudge.FiredAt
		status.ReadyUntil, status.Suggestion = nudge.ReadyUntil, nudge.Suggestion
	} else {
		left := state.minutesLeft()
		status.MinutesLeft = &left
	}
	return status
}

// Print writes the status as a terminal reads it.
func (s Status) Print(out io.Writer, style func(text, color string) string, label func(answer string) string) {
	rows := [][2]string{{"clock", s.Pill.Text}}
	if s.MinutesLeft != nil {
		rows = append(rows, [2]string{"counted", fmt.Sprintf("%s of %s minutes", formatMinutes(s.CountedMinutes), formatMinutes(s.IntervalMinutes))})
	}
	if until, ok := instant(s.ReadyUntil); ok {
		rows = append(rows, [2]string{"ready until", until.Local().Format("15:04")})
	}
	if s.Suggestion != nil {
		said := s.Suggestion.Pursuit + " · " + s.Suggestion.Label
		if s.Suggestion.Error != "" {
			said = s.Suggestion.Message
		}
		rows = append(rows, [2]string{"suggestion", said})
	}
	var counts []string
	for _, answer := range answerValues {
		counts = append(counts, fmt.Sprintf("%s %d", label(answer), s.AnswerCounts[answer]))
	}
	rows = append(rows, [2]string{"answers", strings.Join(counts, " · ")})
	if s.QueuedAnswerCount > 0 {
		rows = append(rows, [2]string{"not saved", fmt.Sprintf("%d, which the watch sends again", s.QueuedAnswerCount)})
	}
	width := 0
	for _, row := range rows {
		width = max(width, len(row[0]))
	}
	for _, row := range rows {
		_, _ = fmt.Fprintf(out, "  %s  %s\n", style(fmt.Sprintf("%-*s", width, row[0]), "dim"), row[1])
	}
	if !s.WatchRunning {
		_, _ = fmt.Fprintln(out, style("  The watch is not running, so the clock stands still.", "yellow"))
	}
}
