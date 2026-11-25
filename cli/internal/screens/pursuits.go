package screens

import (
	"fmt"
	"io"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/datapointchris/gotermboard/printedtable"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

// reasonIndent is how far a pursuit's reason sits in under its row.
const reasonIndent = "    "

// PursuitList prints every pursuit, heaviest first: its weight, goal, when it
// was last done and when it next comes due, then any recent offers whose
// pursuit the register does not hold.
func PursuitList(w io.Writer, list service.PursuitList, offers service.Offers, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Pursuits", gotermstyle.CodeCyan))
	names, goals, lasts := 0, 0, 0
	for _, row := range list.Rows {
		names, goals, lasts = max(names, runes(row.Name)), max(goals, runes(row.Goal)), max(lasts, runes(row.Last))
	}
	tracking := ""
	for _, row := range list.Rows {
		l := line{}.add("  ", "").add(padded(row.Name, names), gotermstyle.CodeWhite)
		if row.Paced {
			l = l.add("  "+strings.Repeat(" ", max(0, 3-runes(row.Weight)))+row.Weight, "")
		} else {
			l = l.add("     ", "")
			tracking = " · tracked is logged and never due"
		}
		l = l.add("  "+padded(row.Goal, goals), "").add("  last "+padded(row.Last, lasts), "")
		if row.Paced {
			late := ""
			if row.Late {
				late = gotermstyle.CodeYellow
			}
			l = l.add("  ", "").add(row.Due, late)
		}
		if row.Standing != "" {
			l = l.add("  "+row.Standing, gotermstyle.CodeYellow)
		}
		pr.cut(l)
		reasonUnder(pr, row.Reason)
	}
	pr.blank()
	pr.wrap(line{}.add("  weight orders what is owed and draws the rest"+tracking+" · ", "").add("doit pursuits edit", gotermstyle.CodeCyan))
	strandedOffers(pr, offers)
	return pr.written
}

// reasonUnder prints a pursuit's reason on the lines under its row, wrapped,
// because a row is one line and a reason is a sentence.
func reasonUnder(pr *printer, reason string) {
	if reason == "" {
		return
	}
	for _, piece := range (line{}.add(reason, "")).wrapped(max(1, pr.widths.Console-len(reasonIndent))) {
		pr.write(reasonIndent + piece.paint(pr.p))
	}
}

// strandedOffers names each recent offer count whose pursuit the register does
// not hold, which no other screen mentions.
func strandedOffers(pr *printer, offers service.Offers) {
	if len(offers.Stranded) == 0 {
		return
	}
	pr.blank()
	pr.wrap(line{}.add("  Offers over the last "+offers.Window+" with no pursuit:", gotermstyle.CodeYellow))
	for _, name := range offers.Stranded {
		for _, machine := range slices.Sorted(maps.Keys(offers.Recent)) {
			if count, held := offers.Recent[machine][name]; held {
				pr.wrap(line{}.add("    ", "").add(name, gotermstyle.CodeYellow).add(fmt.Sprintf("  %d  %s", count, machine), ""))
			}
		}
	}
	pr.wrap(line{}.add("  Renaming a pursuit strands its counts. Naming it back in the register picks them up again.", ""))
}

// Drift prints what each goal asked for over the window against what got
// done, in each pursuit's own unit. untilNow is a window that ends now.
func Drift(w io.Writer, drift service.Drift, untilNow bool, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	opened := "the first record"
	if drift.Since != nil {
		opened = instantText(*drift.Since, "02 Jan 2006 15:04")
	}
	closed := "now"
	if !untilNow {
		closed = instantText(drift.Until, "02 Jan 2006 15:04")
	}
	pr.rule(line{}.add("Drift · "+opened+" to "+closed, gotermstyle.CodeCyan))
	if !slices.ContainsFunc(drift.Rows, func(row service.DriftAmount) bool { return row.Done != 0 }) {
		pr.wrap(line{}.add("Nothing recorded in the window yet — drift needs history before it can say anything.", ""))
		pr.blank()
		return pr.written
	}
	table := printedtable.Table{
		Columns: []printedtable.Column{
			{Title: "pursuit"},
			{Title: "goal", Right: true},
			{Title: "since", Right: true},
			{Title: "asked", Right: true},
			{Title: "done", Right: true},
			{Title: "due", Right: true},
			{Title: "offered", Right: true},
			{Title: "passed", Right: true},
		},
		Width: widths.Console,
	}
	var named [][2]string
	for _, row := range drift.PrintedRows {
		table.Rows = append(table.Rows, []printedtable.Cell{
			{Text: row.Pursuit},
			{Text: row.Goal},
			{Text: row.Since},
			{Text: row.Asked},
			{Text: row.Done},
			{Text: row.Due},
			{Text: row.Offered},
			{Text: row.Passed},
		})
		named = append(named, [2]string{row.Pursuit, row.Reason})
	}
	for _, drawn := range table.Lines(p) {
		pr.write(drawn)
	}
	reasons(pr, named)
	pr.blank()
	return pr.written
}

// instantText is an RFC 3339 instant in layout, read in the offset it carries.
func instantText(instant, layout string) string {
	at, err := time.Parse(time.RFC3339Nano, instant)
	if err != nil {
		return instant
	}
	return at.Format(layout)
}

// Dormant prints each pursuit that has gone quiet for more than three of its
// own intervals, coldest first.
func Dormant(w io.Writer, dormant service.Dormant, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Dormant", gotermstyle.CodeCyan))
	if len(dormant.Dormant) == 0 {
		pr.wrap(line{}.add("Nothing is running cold.", ""))
		pr.blank()
		return pr.written
	}
	width := 0
	for _, row := range dormant.Dormant {
		width = max(width, runes(row.Pursuit))
	}
	for _, row := range dormant.Dormant {
		pr.wrap(line{}.add("  ", "").add(padded(row.Pursuit, width), gotermstyle.CodeWhite).add("  "+row.Last+" · goal "+row.Goal, ""))
		if row.Reason != nil {
			reasonUnder(pr, *row.Reason)
		}
	}
	pr.blank()
	pr.wrap(line{}.add("  Cold is the time since the last one.", ""))
	pr.wrap(line{}.add("  ", "").add("doit pursuits list", gotermstyle.CodeCyan).add(" shows it beside what each owes.", ""))
	pr.blank()
	return pr.written
}

// Evidence prints what each app says was last done for each watched pursuit,
// and which apps could not be asked.
func Evidence(w io.Writer, evidence service.Evidence, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Evidence", gotermstyle.CodeCyan))
	width := 0
	for _, row := range evidence.Rows {
		width = max(width, runes(row.Pursuit))
	}
	for _, row := range evidence.Rows {
		l := line{}.add("  ", "").add(padded(row.Pursuit, width)+"  ", gotermstyle.CodeWhite)
		switch row.Kind {
		case "undeclared":
			l = l.add(row.Text, gotermstyle.CodeYellow)
		case "failed":
			l = l.add(row.Text, gotermstyle.CodeRed)
		case "observed":
			at := ""
			if row.At != nil {
				at = " · " + instantText(*row.At, "02 Jan 15:04")
			}
			l = l.add(row.Text+at, "")
		default:
			l = l.add(row.Text, "")
		}
		pr.wrap(l)
	}
	pr.blank()
	pr.wrap(line{}.add("  The apps are asked on a timer; this asks them now.", ""))
	pr.blank()
	return pr.written
}
