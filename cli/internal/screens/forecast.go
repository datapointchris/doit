package screens

import (
	"fmt"
	"io"
	"strings"

	"github.com/datapointchris/gotermboard/printedtable"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

// generatedWidth is a reading's timestamp to the minute.
const generatedWidth = len("2026-11-02T12:00")

// Forecast prints one reading: each pursuit's occasions over a week and a
// month, its hours over the month, and how much of its goal that reaches,
// beside what each duration was estimated from.
func Forecast(w io.Writer, reading service.Reading, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add(fmt.Sprintf("Forecast · %d min a day", reading.BudgetMinutes), gotermstyle.CodeCyan))
	table := indentedTable(widths, []printedtable.Column{
		{Title: "pursuit"},
		{Title: "/wk", Right: true, Whole: true},
		{Title: "/mo", Right: true, Whole: true},
		{Title: "h/mo", Right: true, Whole: true},
		{Title: "goal"},
		{Title: "of goal", Right: true, Whole: true},
		{Title: "est"},
	})
	for _, row := range reading.Rows {
		goal := "—"
		if row.Goal != nil {
			goal = *row.Goal
		}
		short := ""
		if row.ShortOfGoal {
			short = gotermstyle.CodeYellow
		}
		table.Rows = append(table.Rows, []printedtable.Cell{
			printedtable.C(row.Name, gotermstyle.CodeWhite),
			{Text: row.Week},
			{Text: row.Month},
			{Text: row.Hours},
			{Text: goal},
			printedtable.C(row.OfGoal, short),
			printedtable.C(row.Estimate, codeDim),
		})
	}
	printIndented(pr, table)
	pr.blank()
	pr.wrap(line{}.add(fmt.Sprintf("  %.0f min/day left unspent · %d runs", reading.UnspentMinutesPerDay, reading.Replicates), ""))
	pr.wrap(line{}.add("  estimates: "+reading.Estimates+" · ", "").add("doit pursuits log --minutes", gotermstyle.CodeCyan).
		add(" measures them", ""))
	return pr.written
}

// Forecasts prints every stored reading, oldest first, by when it was taken,
// on which machine, at what budget, and what its estimates rest on.
func Forecasts(w io.Writer, readings []service.Reading, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add("Forecasts", gotermstyle.CodeCyan))
	table := indentedTable(widths, []printedtable.Column{{}, {}, {Right: true, Whole: true}, {}})
	for _, reading := range readings {
		taken := []rune(plain(reading.Generated))
		taken = taken[:min(len(taken), generatedWidth)]
		table.Rows = append(table.Rows, []printedtable.Cell{
			printedtable.C(strings.ReplaceAll(string(taken), "T", " "), gotermstyle.CodeWhite),
			printedtable.C(reading.Machine, codeDim),
			printedtable.C(fmt.Sprintf("%dm/day", reading.BudgetMinutes), codeDim),
			printedtable.C(reading.Estimates, codeDim),
		})
	}
	printIndented(pr, table)
	plural := "s"
	if len(readings) == 1 {
		plural = ""
	}
	pr.blank()
	pr.wrap(line{}.add(fmt.Sprintf("  %d reading%s · ", len(readings), plural), "").
		add("doit forecast trend", gotermstyle.CodeCyan).add(" grades the matured ones", ""))
	return pr.written
}

// Trend prints what the readings whose window has closed predicted for each
// pursuit, beside what the journal recorded.
func Trend(w io.Writer, trend service.Trend, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add(fmt.Sprintf("Trend · %d-day predictions against the journal", trend.Days), gotermstyle.CodeCyan))
	table := indentedTable(widths, []printedtable.Column{
		{Title: "pursuit"},
		{Title: "said", Right: true, Whole: true},
		{Title: "did", Right: true, Whole: true},
		{Title: "error", Right: true, Whole: true},
	})
	for _, row := range trend.Rows {
		code := codeDim
		if row.Missed {
			code = gotermstyle.CodeYellow
		}
		table.Rows = append(table.Rows, []printedtable.Cell{
			printedtable.C(row.Pursuit, gotermstyle.CodeWhite),
			{Text: row.Said},
			{Text: row.Did},
			printedtable.C(row.Error, code),
		})
	}
	printIndented(pr, table)
	pr.blank()
	pr.wrap(line{}.add(fmt.Sprintf("  averaged over %d readings whose %d-day window has closed", trend.Readings, trend.Days), ""))
	pr.wrap(line{}.add("  error is what the journal recorded minus what the forecast predicted", ""))
	return pr.written
}

// indentedTable is a table of columns that prints under a group's indent,
// cut to the layout's width less that indent.
func indentedTable(widths Widths, columns []printedtable.Column) printedtable.Table {
	return printedtable.Table{Columns: columns, Width: max(1, widths.Layout-len(groupIndent))}
}

// printIndented prints each of table's lines under a group's indent.
func printIndented(pr *printer, table printedtable.Table) {
	for _, drawn := range table.Lines(pr.p) {
		pr.write(groupIndent + drawn)
	}
}

// plain is the service's text stripped of terminal controls, so a padded
// column is measured on what prints.
func plain(text string) string { return terminalcontrol.Strip(text) }
