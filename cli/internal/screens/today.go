package screens

import (
	"fmt"
	"io"
	"math"
	"strings"
	"time"

	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	groupIndent = "  "
	itemIndent  = "    "
	columnGap   = 2
	clockWidth  = len("00:00")
	// actorWidth fits the service's longest actor kind, and unrecorded, the word for none.
	actorWidth = max(len("unattended"), len("unrecorded"))
	// dueLabelMax fits a review slug or a Lab id, and clips a longer one.
	dueLabelMax = 20
	// dueTextMin is what a row's text keeps beside the widest label before a
	// handle may sit beside it. A handle that would take more goes on the
	// line under its row.
	dueTextMin = 16
)

// noteCodes color a due row's note by how late it is.
var noteCodes = map[string]string{"due": gotermstyle.CodeYellow, "overdue": gotermstyle.CodeRed}

// Today prints the day: the figures, what it still owes, then what it has had.
//
// Color finds the structure and marks a state, and never fills a whole row.
// The list headings are cyan and each group's name is blue. A done entry's
// hour is green, a due row's note yellow or red by how late it is, and a
// group that could not be read red.
func Today(w io.Writer, answer service.Today, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	day := ""
	if generated, ok := instant(answer.GeneratedAt); ok {
		day = generated.Format("Mon 02 Jan")
	}
	pr.rule(line{}.add("Today", gotermstyle.CodeCyan).add(" · "+day, ""))
	if answer.Quota != nil {
		for _, figure := range quotaLines(*answer.Quota) {
			pr.cut(figure)
		}
	}
	var due []service.DueLane
	for _, lane := range answer.Due {
		if hasSomething(lane.Lane) {
			due = append(due, lane)
		}
	}
	var done []service.Lane
	for _, lane := range answer.Done {
		if hasSomething(lane) {
			done = append(done, lane)
		}
	}
	if len(due) > 0 {
		pr.wrap(line{}.add("STILL DUE", gotermstyle.CodeCyan))
		dueGroups(pr, due)
	}
	if len(done) > 0 {
		if len(due) > 0 {
			pr.blank()
		}
		pr.wrap(line{}.add("DONE", gotermstyle.CodeCyan))
		doneGroups(pr, done)
	}
	return pr.written
}

func hasSomething(lane service.Lane) bool {
	return !lane.Available() || lane.Reason != "" || len(lane.Grid) > 0 || len(lane.Rows) > 0
}

// quotaLines are today's ask, the checklists, what is behind, and each
// catch-up the service marks shown. A figure is green once today has paid it.
func quotaLines(figures service.Quota) []line {
	var lines []line
	for _, parts := range [][]line{askedParts(figures), checklistParts(figures)} {
		if len(parts) > 0 {
			joined := line{}.add(groupIndent, "")
			for index, part := range parts {
				if index > 0 {
					joined = joined.add(" · ", "")
				}
				joined = append(joined, part...)
			}
			lines = append(lines, joined)
		}
	}
	return append(lines, behindLines(figures)...)
}

func askedParts(figures service.Quota) []line {
	if figures.Unavailable != "" {
		return []line{line{}.add("pursuits unavailable", gotermstyle.CodeRed)}
	}
	var parts []line
	if minutes := figures.Minutes; minutes != nil && minutes.Need > 0 {
		noun := "pursuits"
		if minutes.AskedOf == 1 {
			noun = "pursuit"
		}
		parts = append(parts, line{}.
			add(minutesText(minutes.Done), paidCode(minutes.Done, minutes.Need)).
			add(fmt.Sprintf(" of %s across %d %s", minutesText(minutes.Need), minutes.AskedOf, noun), ""))
	}
	if checkoffs := figures.Checkoffs; checkoffs != nil && checkoffs.Need > 0 {
		parts = append(parts, line{}.
			add(fmt.Sprint(pyRound(checkoffs.Done)), paidCode(checkoffs.Done, checkoffs.Need)).
			add(" of "+checkoffsText(checkoffs.Need), ""))
	}
	return parts
}

func paidCode(done, need float64) string {
	if done >= need {
		return gotermstyle.CodeGreen
	}
	return ""
}

func checklistParts(figures service.Quota) []line {
	var parts []line
	for _, entry := range figures.Checklists {
		if entry.Unavailable != "" {
			parts = append(parts, line{}.add(entry.Name+" unavailable", gotermstyle.CodeRed))
		} else {
			parts = append(parts, line{}.add(fmt.Sprintf("%s %d of %d", entry.Name, entry.Done, entry.Total), ""))
		}
	}
	return parts
}

func behindLines(figures service.Quota) []line {
	minutes, checkoffs := figures.Minutes, figures.Checkoffs
	if minutes == nil || checkoffs == nil || (minutes.Behind == 0 && checkoffs.Behind == 0) {
		return nil
	}
	amounts := func(extraMinutes, extraCheckoffs float64) string {
		var said []string
		if pyRound(extraMinutes) != 0 {
			said = append(said, minutesText(extraMinutes))
		}
		if extraCheckoffs != 0 {
			said = append(said, checkoffsText(extraCheckoffs))
		}
		return strings.Join(said, " and ")
	}
	lines := []line{line{}.add(groupIndent+amounts(minutes.Behind, checkoffs.Behind)+" behind", "")}
	for _, entry := range figures.CatchUp {
		if entry.Shown {
			extra := amounts(entry.MinutesPerDay, entry.CheckoffsPerDay)
			lines = append(lines, line{}.add(groupIndent+extra+" more a day catches up in "+entry.Label, ""))
		}
	}
	return lines
}

// minutesText is minutes as a person says them: 45m, 20h, 1h 30m.
func minutesText(minutes float64) string {
	whole := pyRound(minutes)
	hours, rest := whole/60, whole%60
	switch {
	case hours == 0:
		return fmt.Sprintf("%dm", rest)
	case rest == 0:
		return fmt.Sprintf("%dh", hours)
	}
	return fmt.Sprintf("%dh %dm", hours, rest)
}

func checkoffsText(count float64) string {
	if whole := pyRound(count); whole != 1 {
		return fmt.Sprintf("%d checkoffs", whole)
	}
	return "1 checkoff"
}

// pyRound rounds half to even, as Python's round does, so 22.5 minutes reads 22m.
func pyRound(value float64) int { return int(math.RoundToEven(value)) }

// dueRow is one row of the due list, with the row that counts the rest among them.
type dueRow struct {
	label, text, note, urgency, handle string
}

// visibleRows are a group's first row_cap rows, then one saying how many more
// it holds and what to type to see them.
func visibleRows(lane service.DueLane) []dueRow {
	shown := lane.Rows
	if lane.RowCap >= 0 && len(shown) > lane.RowCap {
		shown = shown[:lane.RowCap]
	}
	rows := make([]dueRow, 0, len(shown)+1)
	for _, row := range shown {
		rows = append(rows, dueRow{row.Label, row.Text, row.Note, row.Urgency, row.Handle})
	}
	if hidden := max(lane.Total, len(lane.Rows)) - len(shown); hidden > 0 {
		rows = append(rows, dueRow{label: fmt.Sprintf("%d more", hidden), handle: strings.Join(lane.Hints, " · ")})
	}
	return rows
}

// dueGroups prints one line per outstanding thing: what, how late, what to type.
//
// The note and handle columns are sized across every group, so they line up
// down the whole list. The label column is sized per group, so a group of long
// review slugs does not push a pursuit's description off the line. A handle is
// never clipped, because a clipped command fails when typed, so one that does
// not fit goes on the line under its row.
func dueGroups(pr *printer, groups []service.DueLane) {
	width := pr.widths.Layout
	shown := make([][]dueRow, len(groups))
	var notes []string
	widestLabel, handles := 0, []string{}
	for index, lane := range groups {
		shown[index] = visibleRows(lane)
		for _, row := range shown[index] {
			notes = append(notes, row.note)
			widestLabel = max(widestLabel, runes(row.label))
			if row.handle != "" {
				handles = append(handles, "↳ "+row.handle)
			}
		}
	}
	noteWidth := columnWidth(width, notes, 0.2, 10)
	noteGap := gapAfter(noteWidth)
	widestLabel = min(dueLabelMax, widestLabel)
	handleRoom := width - len(itemIndent) - gapAfter(widestLabel) - dueTextMin - noteGap - 1
	handleWidth := 0
	for _, handle := range handles {
		if runes(handle) <= handleRoom {
			handleWidth = max(handleWidth, runes(handle))
		}
	}
	whatWidth := max(dueTextMin, width-len(itemIndent)-noteGap-gapAfter(handleWidth))
	for index, lane := range groups {
		pr.wrap(line{}.add(groupIndent+lane.Name, codeGroup))
		if !lane.Available() {
			unavailable(pr, lane.Lane)
			continue
		}
		labelWidth := 0
		for _, row := range shown[index] {
			labelWidth = max(labelWidth, runes(row.label))
		}
		labelWidth = min(dueLabelMax, labelWidth)
		textWidth := max(0, whatWidth-gapAfter(labelWidth))
		for _, row := range shown[index] {
			printed := line{}.add(itemIndent, "")
			if labelWidth > 0 {
				printed = printed.add(fitted(row.label, labelWidth, true)+" ", "")
			}
			printed = printed.add(fitted(row.text, textWidth, true), "")
			if noteWidth > 0 {
				printed = printed.add(" ", "").add(fitted(row.note, noteWidth, true), noteCodes[row.urgency])
			}
			handle := ""
			if row.handle != "" {
				handle = "↳ " + row.handle
			}
			inline := handle != "" && runes(handle) <= handleWidth
			if inline {
				printed = printed.add(" "+handle, "")
			}
			pr.cut(printed.rstripped())
			if handle != "" && !inline {
				indent := len(itemIndent) + gapAfter(labelWidth)
				pr.whole(line{}.add(strings.Repeat(" ", max(0, min(indent, width-runes(handle))))+handle, ""))
			}
		}
		if lane.Reason != "" {
			pr.wrap(line{}.add(itemIndent+"partial — "+lane.Reason, gotermstyle.CodeYellow))
		}
	}
}

// gapAfter is a column's width with the space after it, or nothing for a column with no width.
func gapAfter(width int) int {
	if width == 0 {
		return 0
	}
	return width + 1
}

// columnWidth is what a column needs, bounded by its share of the line.
func columnWidth(width int, values []string, share float64, minimum int) int {
	needed := 0
	for _, value := range values {
		needed = max(needed, runes(value))
	}
	return min(needed, max(minimum, int(float64(width)*share)))
}

func unavailable(pr *printer, lane service.Lane) {
	pr.cut(line{}.add(itemIndent, "").add("unavailable — "+lane.Reason, gotermstyle.CodeRed))
}

// doneGroups prints each group under its name, one line per thing done: its
// hour, who did it where its group records that, then what it was. An entry
// whose record kept only the day leaves the hour blank rather than guess one.
func doneGroups(pr *printer, groups []service.Lane) {
	width := pr.widths.Layout
	for _, lane := range groups {
		pr.wrap(line{}.add(groupIndent+lane.Name, codeGroup))
		if !lane.Available() {
			unavailable(pr, lane)
			continue
		}
		if lane.Reason != "" {
			pr.cut(line{}.add(itemIndent, "").add("partial — "+lane.Reason, gotermstyle.CodeYellow))
		}
		actors := 0
		for _, cell := range lane.Grid {
			if cell.ActorKind != "" {
				actors = actorWidth
			}
		}
		used := len(itemIndent) + clockWidth + columnGap
		if actors > 0 {
			used += actors + columnGap
		}
		textWidth := max(10, width-used)
		for _, cell := range lane.Grid {
			hour := ""
			if at, ok := instant(cell.DoneAt); ok {
				hour = at.Format("15:04")
			}
			printed := line{}.add(itemIndent, "").add(hour, gotermstyle.CodeGreen).
				add(strings.Repeat(" ", clockWidth-len(hour)+columnGap), "")
			if actors > 0 {
				who, code := cell.ActorKind, ""
				if who == "" {
					who, code = "unrecorded", codeDim
				}
				printed = printed.add(fitted(who, actors, true), code).add(strings.Repeat(" ", columnGap), "")
			}
			pr.cut(printed.add(fitted(cell.Text, textWidth, false), ""))
		}
	}
}

// instant reads an ISO 8601 time as Python's fromisoformat does, keeping its own offset.
func instant(text string) (time.Time, bool) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", "2006-01-02 15:04:05.999999999Z07:00", "2006-01-02"} {
		if parsed, err := time.Parse(layout, text); err == nil {
			return parsed, true
		}
	}
	return time.Time{}, false
}
