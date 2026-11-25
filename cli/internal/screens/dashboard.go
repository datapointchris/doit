package screens

import (
	"io"
	"math"
	"strings"

	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	codeAlertTitle = "\033[1;91m"
	handleMark     = "↳ "
	// laneLabelMin is the narrowest label gutter, which a wider label widens for its whole lane.
	laneLabelMin = 8
	// A row's text and note are sized as a share of the line, each with a
	// floor, because a fixed count is too tight on a wide terminal and crowds
	// out the text on a narrow one. A note grows only as wide as its widest
	// value, and keeps laneNoteFloor beside an inline handle. A handle that
	// would take the text or the note below its floor goes on the line under
	// its row.
	laneTextShare = 0.3
	laneTextMin   = 20
	laneNoteShare = 0.4
	laneNoteMin   = 22
	laneNoteFloor = 12
	// A grid takes gridColumns columns where each still gets gridMinColumnWidth, and one column where they would not.
	gridColumns        = 2
	gridGutter         = "   "
	gridMinColumnWidth = 24
	// gridMarkWidth is a member's tick or circle and the space after it.
	gridMarkWidth = 2
	// degradedNameWidth is the column a lane that could not be read is named in.
	degradedNameWidth = 12
)

// Dashboard prints each lane in the service's answer, each in its own order
// and none ranked against another. A lane that could not be read is listed under
// the rest with why, never dropped, because a missing lane reads as nothing
// outstanding. The last line names the pursuits behind.
func Dashboard(w io.Writer, answer service.Dashboard, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	day := ""
	if generated, ok := instant(answer.GeneratedAt); ok {
		day = generated.Format(dayFormat)
	}
	pr.rule(line{}.add("Dashboard", gotermstyle.CodeCyan).add(" · "+day, ""))
	var degraded []service.DashboardLane
	for _, lane := range answer.Lanes {
		switch {
		case !lane.Available():
			degraded = append(degraded, lane)
		case !lane.Quiet:
			dashboardLane(pr, lane)
		}
	}
	if len(degraded) > 0 {
		pr.blank()
		for _, lane := range degraded {
			pr.wrap(line{}.add(groupIndent, "").add(padded(lane.Name, degradedNameWidth), gotermstyle.CodeYellow).
				add("unavailable — "+lane.Reason, ""))
		}
	}
	if standing := answer.PursuitStanding; standing != nil {
		code := gotermstyle.CodeYellow
		if standing.Unavailable {
			code = gotermstyle.CodeRed
		}
		// Printed whole, because it ends in a command to type.
		pr.whole(line{}.add(groupIndent+standing.Text, code))
		pr.blank()
	}
	return pr.written
}

// dashboardLane prints one lane: its heading, its grid or its first row_cap
// rows, where to go for the rest, and what it could not read.
func dashboardLane(pr *printer, lane service.DashboardLane) {
	pr.blank()
	code := gotermstyle.CodeCyan
	if lane.Alert {
		code = codeAlertTitle
	}
	heading := line{}.add(lane.Title, code)
	if lane.Meta != "" {
		heading = heading.add("  "+lane.Meta, "")
	}
	pr.wrap(heading)
	switch {
	case len(lane.Grid) > 0:
		laneGrid(pr, lane.Grid)
	case len(lane.Rows) > 0:
		laneRows(pr, lane.Rows[:min(len(lane.Rows), max(lane.RowCap, 0))])
	default:
		pr.wrap(line{}.add(groupIndent+"—", ""))
	}
	if len(lane.Hints) > 0 {
		// Printed whole, so a pane too narrow for the line puts no break inside a command.
		trailer := line{}.add(groupIndent, "")
		for index, hint := range lane.Hints {
			if index > 0 {
				trailer = trailer.add("   ", "")
			}
			trailer = trailer.add("→ "+hint, gotermstyle.CodeBlue)
		}
		pr.whole(trailer)
	}
	if lane.Reason != "" {
		pr.wrap(line{}.add(groupIndent+"partial — "+lane.Reason, gotermstyle.CodeYellow))
	}
}

// laneRows prints a lane's rows as label, text, note and handle columns.
//
// The text and the note keep their floors first. A handle sits inline where
// it fits whole beside both, and under its row where it does not, because a
// clipped command fails when typed. Past the floors, the note takes what is
// left. Every row in a lane shares one set of columns, so its notes line up.
func laneRows(pr *printer, rows []service.LaneRow) {
	width := pr.widths.Layout
	labelWidth := laneLabelMin
	notes := make([]string, len(rows))
	handles := make([]string, len(rows))
	for index, row := range rows {
		labelWidth = max(labelWidth, runes(row.Label))
		notes[index] = row.Note
		if row.Handle != "" {
			handles[index] = handleMark + row.Handle
		}
	}
	lead := len(groupIndent) + labelWidth + 1
	textFloor := max(laneTextMin, int(float64(width)*laneTextShare))
	noteCap := columnWidth(width, notes, laneNoteShare, laneNoteMin)
	handleRoom := width - lead - textFloor - gapAfter(min(noteCap, laneNoteFloor)) - 1
	handleWidth := 0
	for _, handle := range handles {
		if runes(handle) <= handleRoom {
			handleWidth = max(handleWidth, runes(handle))
		}
	}
	room := width - lead - gapAfter(handleWidth)
	noteWidth := min(noteCap, max(0, room-textFloor-1))
	textWidth := max(laneTextMin, room-gapAfter(noteWidth))
	for index, row := range rows {
		printed := line{}.add(groupIndent, "").add(padded(row.Label, labelWidth), gotermstyle.CodeYellow).add(" ", "").
			add(fitted(row.Text, textWidth, true), "")
		if noteWidth > 0 {
			printed = printed.add(" ", "").add(fitted(row.Note, noteWidth, true), noteCodes[row.Urgency])
		}
		handle := handles[index]
		inline := handle != "" && runes(handle) <= handleWidth
		if inline {
			printed = printed.add(" ", "").add(handle, gotermstyle.CodeCyan)
		}
		pr.cut(printed.rstripped())
		if handle != "" && !inline {
			// Left of the text column where that keeps it on one line.
			indent := max(0, min(lead, width-runes(handle)))
			pr.whole(line{}.add(strings.Repeat(" ", indent), "").add(handle, gotermstyle.CodeCyan))
		}
	}
}

// laneGrid prints a whole set, each member ticked or open, down the first
// column and then the second, so it still reads in its order.
func laneGrid(pr *printer, cells []service.GridCell) {
	available := pr.widths.Layout - len(groupIndent)
	columns := 1
	if available >= gridColumns*gridMinColumnWidth {
		columns = gridColumns
	}
	cellWidth := (available - (columns-1)*len(gridGutter)) / columns
	handleWidth := 0
	for _, cell := range cells {
		handleWidth = max(handleWidth, runes(cell.Handle))
	}
	textWidth := cellWidth - gridMarkWidth - gapAfter(handleWidth)
	perColumn := int(math.Ceil(float64(len(cells)) / float64(columns)))
	for offset := range perColumn {
		printed := line{}.add(groupIndent, "")
		for column := range columns {
			index := column*perColumn + offset
			if index >= len(cells) {
				continue
			}
			if column > 0 {
				printed = printed.add(gridGutter, "")
			}
			cell := cells[index]
			if cell.Done {
				printed = printed.add("✓", gotermstyle.CodeGreen)
			} else {
				printed = printed.add("○", gotermstyle.CodeYellow)
			}
			printed = printed.add(" ", "")
			if handleWidth > 0 {
				printed = printed.add(strings.Repeat(" ", handleWidth-runes(cell.Handle))+cell.Handle, gotermstyle.CodeYellow).add(" ", "")
			}
			printed = printed.add(fitted(cell.Text, textWidth, true), "")
		}
		pr.cut(printed.rstripped())
	}
}
