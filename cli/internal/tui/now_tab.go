// Package tui holds the tabs of `doit tui`, each a gotermboard board.
package tui

import (
	"context"
	"errors"
	"strconv"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/datapointchris/gotermboard"

	"github.com/datapointchris/doit/cli/internal/desknudge"
)

// NowTab is the tab of today's rows on the desk nudge panel.
const NowTab = "now"

// TabNames is every tab, in the order Tab cycles them.
func TabNames() []string { return []string{NowTab} }

// panelReadEvery is how often the now tab reads the panel again. A read opens two files on this desk.
const panelReadEvery = 15 * time.Second

// Panel is how the now tab reaches the desk nudge panel, which holds every rule about what a row
// is and what a pick does.
type Panel interface {
	Show() desknudge.Panel
	Pick(ctx context.Context, key string) (desknudge.Panel, error)
}

// DeskPanel is this desk's panel. Show reads it as `doit desk-nudge panel show` does, from the
// files the watch writes, and Pick hands a key to the watch as `doit desk-nudge panel pick` does.
type DeskPanel struct{ Desk *desknudge.Desk }

func (p DeskPanel) Show() desknudge.Panel { return p.Desk.Show() }

// Pick needs no bound of its own. The watch bounds each request and program of a pick, and
// AskPick sets a bound well past those.
func (p DeskPanel) Pick(ctx context.Context, key string) (desknudge.Panel, error) {
	step, err := p.Desk.Paths.AskPick(ctx, key, nil)
	if said, _, ok := desknudge.PickFailure(err); ok {
		return step, errors.New(said)
	}
	return step, err
}

// minutesOffer is a timed pursuit's minutes step: the amount enter logs, and the rows that reopen
// the step above and below it.
type minutesOffer struct {
	log      desknudge.Row
	up, down *desknudge.Row
}

func offerFrom(step desknudge.Panel) (minutesOffer, bool) {
	var offer minutesOffer
	found := false
	for _, row := range step.Rows {
		if row.Key != nil && row.Kind == desknudge.KindLogMinutes && (row.Suggested || !found) {
			offer.log, found = row, true
		}
	}
	if !found || offer.log.Minutes == nil {
		return minutesOffer{}, found
	}
	for _, row := range step.Rows {
		switch {
		case row.Key == nil || row.Kind != desknudge.KindAdjustMinutes || row.Minutes == nil:
		case *row.Minutes > *offer.log.Minutes:
			offer.up = &row
		case *row.Minutes < *offer.log.Minutes:
			offer.down = &row
		}
	}
	return offer, true
}

// pickedMsg is what a pick came to, for the row it acted for.
type pickedMsg struct {
	row  string
	step desknudge.Panel
	err  error
}

// NowBoard is the now tab: the suggestion, the answers while a nudge is ready, and every row of
// today that something completes, each completed by a click or a key.
type NowBoard struct {
	panel Panel
	today *gotermboard.Source[desknudge.Panel]
	// offered is the row whose minutes are open under it, and offer is those minutes. Empty while
	// no row's are.
	offered  string
	offer    minutesOffer
	underWay string
}

func NewNowBoard(p Panel) *NowBoard {
	b := &NowBoard{panel: p}
	b.today = gotermboard.NewSource("desk nudge panel", panelReadEvery, func(context.Context) (desknudge.Panel, string, error) {
		return p.Show(), "", nil
	})
	return b
}

func (b *NowBoard) Sources() []gotermboard.Refresher { return []gotermboard.Refresher{b.today} }
func (b *NowBoard) Groupings() []string              { return nil }
func (b *NowBoard) UnderWay() string                 { return b.underWay }

// Snapshot is the tab's panel document, as `doit desk-nudge panel show --json` prints it.
func (b *NowBoard) Snapshot(time.Time) desknudge.Panel { return b.today.Value() }

// Frame draws the suggestion first, then each completion row under the group the panel names for
// it, in the order the panel lists them.
func (b *NowBoard) Frame(now time.Time, _ string) gotermboard.Frame {
	strip := []gotermboard.Line{gotermboard.ReadStatus([]gotermboard.Status{b.today.Status()}, now)}
	suggested := gotermboard.Group{Key: "suggested", Title: title("up next")}
	var groups []gotermboard.Group
	at := map[string]int{}
	for _, row := range b.today.Value().Rows {
		switch {
		case row.Key == nil:
			strip = append(strip, gotermboard.Line{gotermboard.C(row.Text, gotermboard.Yellow)})
		case row.Kind == desknudge.KindAnswer:
		case row.Suggested:
			suggested.Rows = append(suggested.Rows, b.row(row))
		default:
			name := row.Kind
			if row.Group != nil {
				name = *row.Group
			}
			i, seen := at[name]
			if !seen {
				i, at[name] = len(groups), len(groups)
				groups = append(groups, gotermboard.Group{Key: name, Title: title(name)})
			}
			groups[i].Rows = append(groups[i].Rows, b.row(row))
		}
	}
	if len(suggested.Rows) > 0 {
		groups = append([]gotermboard.Group{suggested}, groups...)
	}
	return gotermboard.Frame{Strip: strip, Columns: []gotermboard.Column{{Title: ""}}, Groups: groups}
}

func title(text string) gotermboard.Line {
	return gotermboard.Line{gotermboard.C(text, gotermboard.Bold)}
}

// row draws a completion row, with the minutes it offers under it while a pick has opened them.
func (b *NowBoard) row(row desknudge.Row) gotermboard.Row {
	drawn := gotermboard.Row{Key: *row.Key, Cells: []gotermboard.Cell{gotermboard.C(row.Text, "")}}
	if b.offered != "" && b.offered == *row.Key {
		said := b.offer.log.Text
		if b.offer.up != nil || b.offer.down != nil {
			said += "   +/- changes it, enter logs it"
		}
		drawn.Second = gotermboard.Line{gotermboard.C(said, gotermboard.Cyan)}
	}
	return drawn
}

// Actions puts each of a ready nudge's answers on a number, in the order the panel lists them.
func (b *NowBoard) Actions() []gotermboard.Action {
	var actions []gotermboard.Action
	for i, answer := range b.answers() {
		number := strconv.Itoa(i + 1)
		actions = append(actions, gotermboard.Action{
			Keys: []string{number}, Label: "answer the nudge: " + answer.Text, Footer: number + " " + answer.Text,
			Run: func(m *gotermboard.Model) tea.Cmd { return b.pick(m.Cursor(), *answer.Key, "answering "+answer.Text) },
		})
	}
	onRow := func(m *gotermboard.Model) bool { _, ok := b.rowAt(m.Cursor()); return ok }
	onSets := func(m *gotermboard.Model) bool { row, ok := b.rowAt(m.Cursor()); return ok && row.UntickKey != nil }
	offering := func(m *gotermboard.Model) bool { return b.offered != "" && m.Cursor() == b.offered }
	return append(actions,
		gotermboard.Action{
			Keys: []string{"enter"}, Label: "complete the row, or log the minutes it offers", Footer: "enter done",
			Run: b.complete, Applies: onRow,
		},
		gotermboard.Action{
			Keys: []string{"u"}, Label: "take back the row's latest set", Footer: "u untick",
			Run: b.untick, Applies: onSets,
		},
		gotermboard.Action{
			Keys: []string{"+", "="}, Label: "offer more minutes", Footer: "+/- minutes",
			Run: func(*gotermboard.Model) tea.Cmd { return b.adjust(b.offer.up) }, Applies: offering,
		},
		gotermboard.Action{
			Keys: []string{"-"}, Label: "offer fewer minutes",
			Run: func(*gotermboard.Model) tea.Cmd { return b.adjust(b.offer.down) }, Applies: offering,
		},
	)
}

func (b *NowBoard) answers() []desknudge.Row {
	var found []desknudge.Row
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && row.Kind == desknudge.KindAnswer {
			found = append(found, row)
		}
	}
	return found
}

func (b *NowBoard) rowAt(key string) (desknudge.Row, bool) {
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && *row.Key == key && row.Kind != desknudge.KindAnswer {
			return row, true
		}
	}
	return desknudge.Row{}, false
}

// complete picks the cursor's row, or logs the minutes a pick on it opened. While another row's
// minutes are open, the line under that row says enter logs them, so enter closes them and
// completes nothing.
func (b *NowBoard) complete(m *gotermboard.Model) tea.Cmd {
	key := m.Cursor()
	switch b.offered {
	case "":
	case key:
		return b.pick(key, *b.offer.log.Key, "logging "+b.offer.log.Text)
	default:
		b.offered = ""
		m.Say("closed the minutes open under another row, so enter again completes this one")
		m.Rebuild()
		return nil
	}
	row, _ := b.rowAt(key)
	return b.pick(key, key, "completing "+row.Text)
}

// untick picks the cursor's checklist row by its untick_key, which takes its latest set back.
func (b *NowBoard) untick(m *gotermboard.Model) tea.Cmd {
	row, _ := b.rowAt(m.Cursor())
	return b.pick(m.Cursor(), *row.UntickKey, "taking back a set of "+row.Text)
}

func (b *NowBoard) adjust(to *desknudge.Row) tea.Cmd {
	if to == nil {
		return nil
	}
	return b.pick(b.offered, *to.Key, "")
}

// pick hands key to the watch in the background, for the row it acts for.
func (b *NowBoard) pick(row, key, doing string) tea.Cmd {
	if b.underWay != "" {
		return nil
	}
	b.underWay = doing
	return func() tea.Msg {
		step, err := b.panel.Pick(context.Background(), key)
		return pickedMsg{row: row, step: step, err: err}
	}
}

func (b *NowBoard) Receive(m *gotermboard.Model, msg tea.Msg) tea.Cmd {
	picked, ok := msg.(pickedMsg)
	if !ok {
		return nil
	}
	b.underWay = ""
	switch {
	case picked.err != nil:
		b.offered = ""
		m.Say(picked.err.Error())
		b.today.Force()
	case picked.step.Step == desknudge.StepMinutes:
		if offer, ok := offerFrom(picked.step); ok {
			b.offered, b.offer = picked.row, offer
		}
	case picked.step.Step == desknudge.StepDone:
		b.offered = ""
		if picked.step.Message != nil {
			m.Say(*picked.step.Message)
		}
		b.today.Force()
	}
	m.Rebuild()
	return nil
}
