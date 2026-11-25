package screens

import (
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
	dayFormat   = "Mon 02 Jan"
	dayWidth    = len("Mon 26 Oct")
	// noActor is what the actor column says of an entry whose record named nobody.
	noActor = "unrecorded"
)

// noteCodes color a row's note by its urgency, as the lane contract names it.
var noteCodes = map[string]string{"due": gotermstyle.CodeYellow, "overdue": gotermstyle.CodeRed}

// hasSomething is a group worth a heading: one that failed, read in part, or holds a row.
func hasSomething(lane service.Lane) bool {
	return !lane.Available() || lane.Reason != "" || len(lane.Grid) > 0 || len(lane.Rows) > 0
}

func unavailable(pr *printer, lane service.Lane) {
	pr.cut(line{}.add(itemIndent, "").add("unavailable — "+lane.Reason, gotermstyle.CodeRed))
}

// doneGroups prints each group under its name, one line per thing done: its
// day where dated asks, its hour, who did it where its group records that,
// then what it was. An entry whose record kept only the day leaves the hour
// blank rather than guess one.
func doneGroups(pr *printer, groups []service.Lane, dated bool) {
	width := pr.widths.Layout
	dateWidth := 0
	if dated {
		dateWidth = dayWidth
	}
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
				actors = max(actors, len(noActor), runes(cell.ActorKind))
			}
		}
		used := len(itemIndent) + clockWidth + columnGap
		if dateWidth > 0 {
			used += dateWidth + columnGap
		}
		if actors > 0 {
			used += actors + columnGap
		}
		textWidth := max(10, width-used)
		for _, cell := range lane.Grid {
			printed := line{}.add(itemIndent, "")
			if dateWidth > 0 {
				day := dayText(cell)
				printed = printed.add(day+strings.Repeat(" ", max(0, dateWidth+columnGap-len(day))), "")
			}
			hour := ""
			if at, ok := instant(cell.DoneAt); ok {
				hour = at.Format("15:04")
			}
			printed = printed.add(hour, gotermstyle.CodeGreen).
				add(strings.Repeat(" ", clockWidth-len(hour)+columnGap), "")
			if actors > 0 {
				who, code := cell.ActorKind, ""
				if who == "" {
					who, code = noActor, codeDim
				}
				printed = printed.add(fitted(who, actors, true), code).add(strings.Repeat(" ", columnGap), "")
			}
			pr.cut(printed.add(fitted(cell.Text, textWidth, false), ""))
		}
	}
}

// dayText is the day a cell was done, where its record kept one.
func dayText(cell service.GridCell) string {
	day := cell.DoneOn
	if day == "" && len(cell.DoneAt) >= len("2006-01-02") {
		day = cell.DoneAt[:len("2006-01-02")]
	}
	parsed, err := time.Parse(time.DateOnly, day)
	if err != nil {
		return ""
	}
	return parsed.Format(dayFormat)
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
