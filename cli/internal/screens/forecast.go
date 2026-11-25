package screens

import (
	"fmt"
	"io"
	"strings"

	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	// pursuitWidth is the forecast tables' name column, which a longer name overruns.
	pursuitWidth = 9
	// goalWidthWithoutGoals is the goal column of a reading taken before goals were recorded.
	goalWidthWithoutGoals = 4
	// generatedWidth is a reading's timestamp to the minute.
	generatedWidth = len("2026-11-02T12:00")
)

// Forecast prints one reading: each pursuit's occasions over a week and a
// month, its hours over the month, and how much of its goal that reaches,
// beside what each duration was estimated from.
func Forecast(w io.Writer, reading service.Reading, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.rule(line{}.add(fmt.Sprintf("Forecast · %d min a day", reading.BudgetMinutes), gotermstyle.CodeCyan))
	goalWidth := 0
	for _, row := range reading.Rows {
		if row.Goal != nil {
			goalWidth = max(goalWidth, runes(*row.Goal))
		}
	}
	if goalWidth == 0 {
		goalWidth = goalWidthWithoutGoals
	}
	pr.wrap(line{}.add(groupIndent, "").add(fmt.Sprintf("%-*s %5s %6s %6s  %-*s %8s  %-9s",
		pursuitWidth, "pursuit", "/wk", "/mo", "h/mo", goalWidth, "goal", "of goal", "est"), codeDim))
	for _, row := range reading.Rows {
		goal := "—"
		if row.Goal != nil {
			goal = plain(*row.Goal)
		}
		short := ""
		if row.ShortOfGoal {
			short = gotermstyle.CodeYellow
		}
		pr.wrap(line{}.add(groupIndent, "").
			add(fmt.Sprintf("%-*s ", pursuitWidth, plain(row.Name)), gotermstyle.CodeWhite).
			add(fmt.Sprintf("%5s %6s ", plain(row.Week), plain(row.Month)), "").
			add(fmt.Sprintf("%6s  ", plain(row.Hours)), "").
			add(fmt.Sprintf("%-*s ", goalWidth, goal), "").
			add(fmt.Sprintf("%8s  ", plain(row.OfGoal)), short).
			add(row.Estimate, codeDim))
	}
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
	for _, reading := range readings {
		taken := []rune(plain(reading.Generated))
		taken = taken[:min(len(taken), generatedWidth)]
		pr.wrap(line{}.add(groupIndent, "").
			add(strings.ReplaceAll(string(taken), "T", " ")+"  ", gotermstyle.CodeWhite).
			add(fmt.Sprintf("%-10s %4dm/day  ", plain(reading.Machine), reading.BudgetMinutes), codeDim).
			add(reading.Estimates, codeDim))
	}
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
	pr.wrap(line{}.add(groupIndent, "").add(fmt.Sprintf("%-*s %7s %7s %8s", pursuitWidth, "pursuit", "said", "did", "error"), codeDim))
	for _, row := range trend.Rows {
		code := codeDim
		if row.Missed {
			code = gotermstyle.CodeYellow
		}
		pr.wrap(line{}.add(groupIndent, "").
			add(fmt.Sprintf("%-*s ", pursuitWidth, plain(row.Pursuit)), gotermstyle.CodeWhite).
			add(fmt.Sprintf("%7s %7s ", plain(row.Said), plain(row.Did)), "").
			add(fmt.Sprintf("%8s", plain(row.Error)), code))
	}
	pr.blank()
	pr.wrap(line{}.add(fmt.Sprintf("  averaged over %d readings whose %d-day window has closed", trend.Readings, trend.Days), ""))
	pr.wrap(line{}.add("  error is what the journal recorded minus what the forecast predicted", ""))
	return pr.written
}

// plain is the service's text stripped of terminal controls, so a padded
// column is measured on what prints.
func plain(text string) string { return terminalcontrol.Strip(text) }
