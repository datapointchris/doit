package screens

import (
	"fmt"
	"io"
	"regexp"
	"strings"
	"time"

	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

// unknownActor is the kind the service names for an actor whose spelling it does not recognize.
const unknownActor = "unknown"

// Happened prints what every record says happened over the window: which
// records were asked and which answered, then each group that holds
// something. openEnded is a window that runs to now, which the title says
// rather than naming its last minute. A window spanning several days puts
// each entry's day beside its hour.
func Happened(w io.Writer, answer service.Happened, openEnded bool, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Happened", gotermstyle.CodeCyan).add(" · "+windowTitle(answer, openEnded), ""))
	for _, verdict := range verdictLines(answer, widths.Layout) {
		pr.cut(verdict)
	}
	var shown []service.Lane
	for _, lane := range answer.Groups {
		if hasSomething(lane) {
			shown = append(shown, lane)
		}
	}
	if len(shown) > 0 {
		pr.blank()
		doneGroups(pr, shown, answer.Days > 1)
	}
	return pr.written
}

// windowTitle names both ends of the window. The service starts a window
// asked with no start at the Unix epoch, which reads as the earliest record.
func windowTitle(answer service.Happened, openEnded bool) string {
	const minute = "Mon 02 Jan 15:04"
	start := answer.Since
	if since, ok := instant(answer.Since); ok {
		start = since.Format(minute)
		if since.Equal(time.Unix(0, 0)) {
			start = "the earliest record"
		}
	}
	stop := "now"
	if !openEnded {
		stop = answer.Until
		if until, ok := instant(answer.Until); ok {
			stop = until.Format(minute)
		}
	}
	return start + " to " + stop
}

// verdictLines name every record asked, wrapped under their own indent
// rather than cut, then how many answered, which only in part, and who did
// what.
func verdictLines(answer service.Happened, width int) []line {
	var lines []line
	if len(answer.Asked) == 0 {
		lines = append(lines, line{}.add(groupIndent+"no record to ask", ""))
	}
	lead := groupIndent + "asked "
	for _, named := range filled(strings.Join(answer.Asked, ", "), width, lead, strings.Repeat(" ", len(lead))) {
		lines = append(lines, line{}.add(named, ""))
	}
	var recording []string
	unknown := 0
	for _, lane := range answer.Groups {
		recorded := false
		for _, cell := range lane.Grid {
			recorded = recorded || cell.ActorKind != ""
			if cell.ActorKind == unknownActor {
				unknown++
			}
		}
		if recorded {
			recording = append(recording, lane.Name)
		}
	}
	answered := line{}.add(groupIndent, "")
	if failed := len(answer.Unavailable); failed > 0 {
		answered = answered.add(fmt.Sprintf("%d of %d answered", len(answer.Asked)-failed, len(answer.Asked)), gotermstyle.CodeRed)
	} else {
		answered = answered.add(fmt.Sprintf("all %d answered", len(answer.Asked)), "")
	}
	if len(answer.Partial) > 0 {
		answered = answered.add(" · "+strings.Join(answer.Partial, ", ")+" partial", gotermstyle.CodeYellow)
	}
	if len(recording) > 0 {
		answered = answered.add(" · actor recorded by "+strings.Join(recording, ", "), "")
	} else {
		answered = answered.add(" · no actor recorded", "")
	}
	if unknown > 0 {
		answered = answered.add(fmt.Sprintf(" · %d by an actor of unknown kind", unknown), gotermstyle.CodeYellow)
	}
	return append(lines, answered)
}

// chunks are runs of whitespace and runs of anything else, as Python's
// textwrap splits a text that may break on neither a hyphen nor inside a word.
var chunks = regexp.MustCompile(`\s+|\S+`)

// filled is text wrapped as Python's textwrap.fill wraps it with
// break_long_words and break_on_hyphens off: whole words, greedily, to width
// cells, the first line after initial and the rest after subsequent. A word
// wider than a line has a line of its own.
func filled(text string, width int, initial, subsequent string) []string {
	pending := chunks.FindAllString(text, -1)
	var lines []string
	for len(pending) > 0 {
		indent := initial
		if len(lines) > 0 {
			indent = subsequent
			if strings.TrimSpace(pending[0]) == "" {
				pending = pending[1:]
			}
		}
		room := width - runes(indent)
		var current []string
		used := 0
		for len(pending) > 0 && used+runes(pending[0]) <= room {
			used += runes(pending[0])
			current, pending = append(current, pending[0]), pending[1:]
		}
		if len(current) == 0 && len(pending) > 0 && runes(pending[0]) > room {
			current, pending = append(current, pending[0]), pending[1:]
		}
		if len(current) > 0 && strings.TrimSpace(current[len(current)-1]) == "" {
			current = current[:len(current)-1]
		}
		if len(current) > 0 {
			lines = append(lines, indent+strings.Join(current, ""))
		}
	}
	return lines
}
