package screens

import (
	"fmt"
	"io"
	"strings"

	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	// statusWidth fits a status up to `overdue 999d` and a space. A longer one runs into the handle.
	statusWidth = 13
	// reviewIDWidth is the column a review entry's id fills on a browse view.
	reviewIDWidth = 20
	// maintenanceBlock is the register's block that the nudge and the empty views speak for.
	maintenanceBlock = "items"
)

// ReviewList prints every register entry and its status under its block's
// heading, due or not, then any recorded done dates that no entry holds.
func ReviewList(w io.Writer, review service.Review, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	if len(review.Entries) == 0 {
		emptyRegister(pr)
	}
	for _, block := range review.Blocks {
		entries := inBlock(review.Entries, block.Name, false)
		if len(entries) > 0 || block.Name == maintenanceBlock {
			pr.rule(line{}.add(block.Title+" — full register", gotermstyle.CodeCyan))
		}
		for _, entry := range entries {
			reviewEntry(pr, entry)
		}
	}
	if len(review.Stranded) > 0 {
		pr.wrap(line{}.add("Recorded done dates with no register item: "+strings.Join(review.Stranded, ", "), gotermstyle.CodeYellow))
		pr.wrap(line{}.add("  Renaming an item strands its history. Naming it back in the register picks the history up again.", ""))
	}
	return pr.written
}

// ReviewDue prints what is due under its block's heading, most overdue first,
// or that nothing is and which entry comes due next.
func ReviewDue(w io.Writer, review service.Review, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	if len(review.Entries) == 0 {
		emptyRegister(pr)
		return pr.written
	}
	anyDue := false
	for _, block := range review.Blocks {
		entries := inBlock(review.Entries, block.Name, true)
		if len(entries) == 0 {
			continue
		}
		anyDue = true
		pr.rule(line{}.add(block.Title+" — what's due", gotermstyle.CodeCyan))
		for _, entry := range entries {
			reviewEntry(pr, entry)
		}
	}
	if anyDue {
		pr.wrap(line{}.add("Mark one done:  ", "").add("doit review done <id>", gotermstyle.CodeCyan))
		return pr.written
	}
	title := maintenanceBlock
	for _, block := range review.Blocks {
		if block.Name == maintenanceBlock {
			title = block.Title
		}
	}
	pr.rule(line{}.add(title+" — what's due", gotermstyle.CodeCyan))
	pr.wrap(line{}.add("✓", gotermstyle.CodeGreen).add(" All caught up.", ""))
	soonest := review.Entries[0]
	pr.wrap(line{}.add("  Next up: ", "").add(soonest.ID, gotermstyle.CodeYellow).add(" "+soonest.Status+".", ""))
	return pr.written
}

// ReviewNudge prints the roster a new shell shows: each due maintenance entry
// on one line, with the command that settles it where the line holds it.
func ReviewNudge(w io.Writer, nudge service.Nudge, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	pr.whole(line{}.add(fmt.Sprintf("Review · %d due", nudge.DueTotal), gotermstyle.CodeCyan))
	width := 0
	for _, entry := range nudge.Due {
		width = max(width, runes(entry.ID))
	}
	width += 2
	for _, entry := range nudge.Due {
		l := line{}.add("  ", "").add(padded(entry.ID, width), gotermstyle.CodeYellow)
		handle := ""
		if entry.Command != "" {
			handle = "↳ " + entry.Command
		}
		switch {
		case handle != "" && 2+width+statusWidth+runes(handle) <= widths.Console:
			l = l.add(padded(entry.Status, statusWidth), "").add(handle, gotermstyle.CodeCyan)
		case handle != "" && 2+width+runes(handle) <= widths.Console:
			l = l.add(handle, gotermstyle.CodeCyan)
		default:
			l = l.add(entry.Status, "")
		}
		pr.cut(l)
	}
	if more := nudge.DueTotal - len(nudge.Due); more > 0 {
		pr.wrap(line{}.add(fmt.Sprintf("  +%d more  ·  ", more), "").add("doit review due", gotermstyle.CodeCyan))
	}
	pr.blank()
	return pr.written
}

func emptyRegister(pr *printer) {
	pr.wrap(line{}.add("The review register has no items yet. Add some with ", "").add("doit review edit", gotermstyle.CodeCyan).add(".", ""))
}

// inBlock is the entries of block, only the due ones where onlyDue says so.
func inBlock(entries []service.ReviewEntry, block string, onlyDue bool) []service.ReviewEntry {
	var kept []service.ReviewEntry
	for _, entry := range entries {
		if entry.Block == block && (entry.Due || !onlyDue) {
			kept = append(kept, entry)
		}
	}
	return kept
}

// reviewEntry prints one entry: its id, status and cadence, then what it is
// and the command that settles it.
func reviewEntry(pr *printer, entry service.ReviewEntry) {
	meta := "every " + entry.Cadence
	if entry.Last != nil && *entry.Last != "" {
		meta += " · last " + *entry.Last
	}
	pr.wrap(line{}.add("  ", "").add(padded(entry.ID, reviewIDWidth), gotermstyle.CodeYellow).add("  "+entry.Status+"  ·  "+meta, ""))
	pr.wrap(line{}.add("      "+entry.Desc, ""))
	if entry.Command != "" {
		pr.wrap(line{}.add("      ", "").add("↳ "+entry.Command, gotermstyle.CodeCyan))
	}
	pr.blank()
}

// SourcesList prints each configured source under its block, with whether
// its read answered and how many lanes it gave.
func SourcesList(w io.Writer, sources service.Sources, widths Widths, p gotermstyle.Palette) error {
	pr := &printer{w: w, p: p, widths: widths}
	if len(sources.Answers) == 0 {
		pr.wrap(line{}.add("No sources configured. Write them with ", "").add("doit sources edit", gotermstyle.CodeCyan).add(".", ""))
		return pr.written
	}
	width := 0
	for _, answer := range sources.Answers {
		width = max(width, runes(answer.ID))
	}
	for _, block := range []string{"outstanding", "happened"} {
		heading := false
		for _, answer := range sources.Answers {
			if answer.Block != block {
				continue
			}
			if !heading {
				pr.wrap(line{}.add(block, gotermstyle.CodeCyan))
				heading = true
			}
			l := line{}.add("  ", "").add(padded(answer.ID, width), gotermstyle.CodeYellow)
			switch {
			case !answer.Answered:
				l = l.add("  "+answer.Reason, gotermstyle.CodeRed)
			case answer.Lanes > 0:
				l = l.add(fmt.Sprintf("  %d lanes via %s", answer.Lanes, answer.Via), "")
			default:
				l = l.add("  answered, but offered no lanes", gotermstyle.CodeYellow)
			}
			pr.cut(l)
		}
	}
	return pr.written
}
