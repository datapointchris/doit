package screens

import (
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/datapointchris/gotermboard/printedtable"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	gutter = "   "
	// readableChoiceWidth is the room a choice needs to say what it is. Below
	// it, the standing columns give way one at a time.
	readableChoiceWidth = 30
	// minChoiceWidth is what a choice keeps once every column that can give way has.
	minChoiceWidth = 12
)

// offerColumn is one column of `doit next` before the item.
type offerColumn struct {
	heading string
	cell    func(service.OfferRow) string
	right   bool
	// code is the cell's color on an offered row.
	code string
	// marksLate colors the cell yellow on a row that is late.
	marksLate bool
	// dropOrder is when the column gives way in a narrow pane, from 1, or 0 for never.
	dropOrder int
}

// The name and the date are what the screen is read for, so they never give way.
var offerColumns = []offerColumn{
	{heading: "pursuit", cell: func(r service.OfferRow) string { return r.Name }, code: gotermstyle.CodeWhite},
	{heading: "wt", cell: func(r service.OfferRow) string { return r.Weight }, right: true, dropOrder: 1},
	{heading: "goal", cell: func(r service.OfferRow) string { return r.Goal }, dropOrder: 2},
	{heading: "done", cell: func(r service.OfferRow) string { return r.Tally }, dropOrder: 3},
	{heading: "due", cell: func(r service.OfferRow) string { return r.Due }, marksLate: true},
}

// lastDrop is the highest dropOrder in offerColumns.
func lastDrop() int {
	last := 0
	for _, column := range offerColumns {
		last = max(last, column.dropOrder)
	}
	return last
}

// Next prints what to do now: today's events, then every pursuit, the offered
// ones first in the draw's order, and the rest dimmed under them.
func Next(w io.Writer, answer service.Next, now time.Time, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("What now", gotermstyle.CodeCyan).add(" · "+now.Format("Mon 02 Jan"), ""))
	// One per line. Joined on one line, two dates read as one event.
	for _, event := range answer.Context {
		pr.cut(line{}.add(gutter+event, gotermstyle.CodeMagenta))
	}
	if answer.ContextError != "" {
		// Said rather than left blank, because a blank reads as a quiet day.
		pr.whole(line{}.add(gutter+"events unavailable — "+answer.ContextError, gotermstyle.CodeYellow))
	}
	pr.blank()
	offers(pr, answer.OfferRows)
	return pr.written
}

// offers prints every pursuit as a row. The standing columns come first at
// fixed widths, so a narrow pane eats the tail of a choice rather than which
// pursuit the row is or how late it is.
func offers(pr *printer, rows []service.OfferRow) {
	if len(rows) == 0 {
		return
	}
	var offered, rest []service.OfferRow
	for _, row := range rows {
		if row.Offered {
			offered = append(offered, row)
		} else {
			rest = append(rest, row)
		}
	}
	widths := make([]int, len(offerColumns))
	for index, column := range offerColumns {
		widths[index] = runes(column.heading)
		for _, row := range rows {
			widths[index] = max(widths[index], runes(column.cell(row)))
		}
	}
	shown := make([]int, len(offerColumns))
	for index := range offerColumns {
		shown[index] = index
	}
	room := func() int {
		used := len(gutter)
		for _, index := range shown {
			used += widths[index] + len(gutter)
		}
		return pr.widths.Layout - used
	}
	for drop := 1; drop <= lastDrop() && room() < readableChoiceWidth; drop++ {
		kept := shown[:0:0]
		for _, index := range shown {
			if offerColumns[index].dropOrder != drop {
				kept = append(kept, index)
			}
		}
		shown = kept
	}
	body := max(room(), minChoiceWidth)
	cell := func(text string, index int) string {
		if pad := widths[index] - runes(text); pad > 0 {
			if offerColumns[index].right {
				return strings.Repeat(" ", pad) + text
			}
			return text + strings.Repeat(" ", pad)
		}
		return text
	}

	headings := make([]string, len(shown))
	for position, index := range shown {
		headings[position] = cell(offerColumns[index].heading, index)
	}
	pr.cut(line{}.add(gutter+strings.Join(headings, gutter)+gutter+"item", gotermstyle.CodeBold))

	// A pursuit offering several choices is a block, and blocks printed flush
	// run the last choice of one into the name of the next. So the gap goes in
	// wherever any block is taller than a line.
	spaced := false
	for _, row := range offered {
		spaced = spaced || len(row.Choices) > 1
	}
	for position, row := range offered {
		if spaced && position > 0 {
			pr.blank()
		}
		for choiceIndex, choice := range row.Choices {
			printed := line{}.add(gutter, "")
			// The standing is the pursuit's, not each choice's, so it is said once.
			for _, index := range shown {
				text := strings.Repeat(" ", widths[index])
				if choiceIndex == 0 {
					text = cell(offerColumns[index].cell(row), index)
				}
				code := offerColumns[index].code
				if offerColumns[index].marksLate && row.Late {
					code = gotermstyle.CodeYellow
				}
				printed = printed.add(text, code).add(gutter, "")
			}
			code := gotermstyle.CodeGreen
			if row.Failed {
				code = gotermstyle.CodeRed
			}
			pr.cut(printed.add(fitted(choice, body, false), code))
		}
	}

	if len(rest) > 0 {
		pr.blank()
	}
	for _, row := range rest {
		printed := line{}.add(gutter, codeDim)
		for position, index := range shown {
			if position > 0 {
				printed = printed.add(gutter, codeDim)
			}
			code := codeDim
			if offerColumns[index].marksLate && row.Late {
				code = codeDimYellow
			}
			printed = printed.add(cell(offerColumns[index].cell(row), index), code)
		}
		pr.cut(printed)
	}
}

// Explain prints the draw with every number behind it: each pursuit owed
// first, then the reasons the register gives, then how to read the table.
func Explain(w io.Writer, answer service.Next, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Why these", gotermstyle.CodeCyan))
	draw := answer.DrawID
	if len(draw) > 8 {
		draw = draw[:8]
	}
	pr.wrap(line{}.add("draw "+draw, ""))
	pr.blank()

	table := printedtable.Table{
		Columns: []printedtable.Column{
			{},
			{Title: "pursuit"},
			{Title: "wt", Right: true},
			{Title: "goal", Right: true},
			{Title: "last", Right: true},
			{Title: "due", Right: true},
			{Title: "pick", Right: true},
		},
		Width: widths.Console,
		Gap:   2,
	}
	for _, row := range answer.ExplainRows {
		chosen := printedtable.C("", "")
		if row.Offered {
			chosen = printedtable.C("●", gotermstyle.CodeGreen)
		}
		name := printedtable.C(row.Name, "")
		if row.Skipped {
			name.Code = gotermstyle.CodeYellow
		}
		table.Rows = append(table.Rows, []printedtable.Cell{
			chosen, name, {Text: row.Weight}, {Text: row.Goal}, {Text: row.Last}, {Text: row.Due}, {Text: row.Pick},
		})
	}
	for _, drawn := range table.Lines(p) {
		pr.write(drawn)
	}
	named := make([][2]string, 0, len(answer.ExplainRows))
	for _, row := range answer.ExplainRows {
		named = append(named, [2]string{row.Name, row.Reason})
	}
	reasons(pr, named)
	pr.blank()
	pr.wrap(line{}.add(fmt.Sprintf("  every owed row is shown, heaviest first · the rest is drawn by weight to fill %d rows", answer.DrawSize), ""))
	pr.wrap(line{}.add("  pick is the chance of being drawn ", "").add("first", gotermstyle.CodeBold).
		add(" · ", "").add("yellow", gotermstyle.CodeYellow).add(" is skipped", ""))
	pr.wrap(line{}.add("  ", "").add("doit next --json", gotermstyle.CodeCyan).
		add(" carries the raw balance every column here is derived from", ""))
	return pr.written
}

// reasons prints each pursuit's reason under a table, whose columns have no
// room for a sentence. A reason wraps beside its name. named holds each
// pursuit's name and reason, and a pursuit with no reason prints nothing.
func reasons(pr *printer, named [][2]string) {
	nameWidth, reasonWidth := 0, 0
	for _, row := range named {
		if row[1] != "" {
			nameWidth = max(nameWidth, runes(row[0]))
			reasonWidth = max(reasonWidth, cells(row[1]))
		}
	}
	if nameWidth == 0 {
		return
	}
	reasonWidth = max(1, min(reasonWidth, pr.widths.Console-nameWidth-2))
	pr.blank()
	for _, row := range named {
		if row[1] == "" {
			continue
		}
		for index, piece := range (line{}.add(row[1], "")).wrapped(reasonWidth) {
			name := strings.Repeat(" ", nameWidth)
			if index == 0 {
				name = fitted(row[0], nameWidth, true)
			}
			pr.write(name + "  " + piece.paint(pr.p))
		}
	}
}
