package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"slices"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/datapointchris/gotermboard"
)

// panelStep is one step of `doit desk-nudge panel`, as doit's schemas/desk-nudge-panel.schema.json
// gives it.
type panelStep struct {
	SchemaVersion int        `json:"schema_version"`
	Step          string     `json:"step"`
	Rows          []panelRow `json:"rows"`
	Message       *string    `json:"message"`
}

type panelRow struct {
	Key       *string `json:"key"`
	Kind      string  `json:"kind"`
	Text      string  `json:"text"`
	Suggested bool    `json:"suggested"`
}

// The steps and row kinds the now tab acts on, as doit's panel names them.
const (
	stepMinutes    = "minutes"
	stepDone       = "done"
	kindAnswer     = "answer"
	kindLog        = "log_minutes"
	kindAdjust     = "adjust_minutes"
	keySeparator   = ":"
	panelReadEvery = 15 * time.Second
	// pickWithin bounds one pick, which may complete a habit through life or log through doit's service.
	pickWithin = time.Minute
)

// panel is how the now tab reaches doit, which holds every rule about what a row is and what a
// pick does.
type panel interface {
	show(ctx context.Context) (panelStep, error)
	pick(ctx context.Context, key string) (panelStep, error)
}

// doitPanel runs `doit desk-nudge panel show --json` and `doit desk-nudge panel pick <key> --json`.
type doitPanel struct{}

func (doitPanel) show(ctx context.Context) (panelStep, error) { return runPanel(ctx, "show", "--json") }

func (doitPanel) pick(ctx context.Context, key string) (panelStep, error) {
	return runPanel(ctx, "pick", key, "--json")
}

func runPanel(ctx context.Context, args ...string) (panelStep, error) {
	cmd := exec.CommandContext(ctx, "doit", append([]string{"desk-nudge", "panel"}, args...)...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return panelStep{}, errors.New(gotermboard.LastLine(stderr.Bytes(), err))
	}
	var step panelStep
	if err := json.Unmarshal(out, &step); err != nil {
		return panelStep{}, errors.New("doit desk-nudge panel printed no step: " + err.Error())
	}
	return step, nil
}

// The groups the now tab draws completion rows in, in order, by the kind doit gives each row.
var kindGroups = []struct{ kind, title string }{
	{"pursuit", "pursuits"},
	{"habit", "habits"},
	{"practice", "practices"},
	{"review", "review"},
}

// The answers to a ready nudge, each on a key and a footer hint, by the value its row key carries.
var answers = []struct{ key, value, label string }{
	{"1", "good_timing", "good timing"},
	{"2", "too_soon", "too soon"},
	{"3", "too_late", "too late"},
	{"4", "bothered", "bothered me"},
}

// minutesOffer is a timed pursuit's minutes step, held under its row until a log or another row's
// pick ends it.
type minutesOffer struct {
	log      panelRow
	up, down *panelRow
}

// offerFrom reads a minutes step. Where it offers several amounts, the one at the center of its
// ±5 rows is the one enter logs.
func offerFrom(step panelStep) (minutesOffer, bool) {
	var offer minutesOffer
	var logs, adjusts []panelRow
	for _, row := range step.Rows {
		switch {
		case row.Key == nil:
		case row.Kind == kindLog:
			logs = append(logs, row)
		case row.Kind == kindAdjust:
			adjusts = append(adjusts, row)
		}
	}
	if len(logs) == 0 {
		return minutesOffer{}, false
	}
	// doit leaves out a −5 that would reach zero, so a lone adjust row is the +5.
	slices.SortFunc(adjusts, func(a, b panelRow) int { return amountOf(*a.Key) - amountOf(*b.Key) })
	switch len(adjusts) {
	case 1:
		offer.up = &adjusts[0]
	case 2:
		offer.down, offer.up = &adjusts[0], &adjusts[1]
	}
	offer.log = logs[0]
	if center, ok := centerOf(offer); ok {
		for _, row := range logs {
			if amountOf(*row.Key) == center {
				offer.log = row
			}
		}
	}
	return offer, true
}

// amountOf is the minutes a minutes row's key names: `log_minutes:45:pursuit:read` names 45.
func amountOf(key string) int {
	parts := strings.SplitN(key, keySeparator, 3)
	if len(parts) < 2 {
		return 0
	}
	amount := 0
	for _, digit := range parts[1] {
		if digit < '0' || digit > '9' {
			return 0
		}
		amount = amount*10 + int(digit-'0')
	}
	return amount
}

func centerOf(offer minutesOffer) (int, bool) {
	switch {
	case offer.up != nil && offer.down != nil:
		return (amountOf(*offer.up.Key) + amountOf(*offer.down.Key)) / 2, true
	case offer.up != nil:
		return amountOf(*offer.up.Key) - 5, true
	}
	return 0, false
}

// pickedMsg is what a pick came to, for the row it acted for.
type pickedMsg struct {
	row  string
	step panelStep
	err  error
}

// nowBoard is the now tab: the suggestion, the answers while a nudge is ready, and every row of
// today that something completes, each completed by a click or a key.
type nowBoard struct {
	panel    panel
	today    *gotermboard.Source[panelStep]
	offers   map[string]minutesOffer
	underWay string
}

func newNowBoard(p panel) *nowBoard {
	b := &nowBoard{panel: p, offers: map[string]minutesOffer{}}
	b.today = gotermboard.NewSource("doit desk-nudge panel", panelReadEvery, func(ctx context.Context) (panelStep, string, error) {
		step, err := p.show(ctx)
		return step, "", err
	})
	return b
}

func (b *nowBoard) Sources() []gotermboard.Refresher { return []gotermboard.Refresher{b.today} }
func (b *nowBoard) Groupings() []string              { return nil }
func (b *nowBoard) UnderWay() string                 { return b.underWay }

func (b *nowBoard) Frame(now time.Time, _ string) gotermboard.Frame {
	strip := []gotermboard.Line{gotermboard.ReadStatus([]gotermboard.Status{b.today.Status()}, now)}
	suggested := gotermboard.Group{Key: "suggested", Title: gotermboard.Line{gotermboard.C("up next", gotermboard.Bold)}}
	byKind := map[string][]gotermboard.Row{}
	for _, row := range b.today.Value().Rows {
		switch {
		case row.Key == nil:
			strip = append(strip, gotermboard.Line{gotermboard.C(row.Text, gotermboard.Yellow)})
		case row.Kind == kindAnswer:
		case row.Suggested:
			suggested.Rows = append(suggested.Rows, b.row(row))
		default:
			byKind[row.Kind] = append(byKind[row.Kind], b.row(row))
		}
	}
	var groups []gotermboard.Group
	if len(suggested.Rows) > 0 {
		groups = append(groups, suggested)
	}
	for _, g := range kindGroups {
		if rows := byKind[g.kind]; len(rows) > 0 {
			groups = append(groups, gotermboard.Group{Key: g.kind, Title: gotermboard.Line{gotermboard.C(g.title, gotermboard.Bold)}, Rows: rows})
		}
	}
	return gotermboard.Frame{Strip: strip, Columns: []gotermboard.Column{{Title: ""}}, Groups: groups}
}

// row draws a completion row, with the minutes it offers under it while a pick has opened them.
func (b *nowBoard) row(row panelRow) gotermboard.Row {
	// A habit's text opens on `habit · `, which its group's title already says.
	text := strings.TrimPrefix(row.Text, row.Kind+" · ")
	drawn := gotermboard.Row{Key: *row.Key, Cells: []gotermboard.Cell{gotermboard.C(text, "")}}
	if offer, open := b.offers[*row.Key]; open {
		said := offer.log.Text
		if offer.up != nil || offer.down != nil {
			said += "   +/- changes it, enter logs it"
		}
		drawn.Second = gotermboard.Line{gotermboard.C(said, gotermboard.Cyan)}
	}
	return drawn
}

func (b *nowBoard) Actions() []gotermboard.Action {
	actions := make([]gotermboard.Action, 0, len(answers)+3)
	for _, answer := range answers {
		actions = append(actions, gotermboard.Action{
			Keys: []string{answer.key}, Label: "answer the nudge: " + answer.label, Footer: answer.key + " " + answer.label,
			Run: func(m *gotermboard.Model) tea.Cmd {
				key, _ := b.answerKey(answer.value)
				return b.pick(m.Cursor(), key, "answering "+answer.label)
			},
			Applies: func(*gotermboard.Model) bool {
				_, ready := b.answerKey(answer.value)
				return ready
			},
		})
	}
	onRow := func(m *gotermboard.Model) bool { _, ok := b.rowAt(m.Cursor()); return ok }
	offering := func(m *gotermboard.Model) bool { _, ok := b.offers[m.Cursor()]; return ok }
	return append(actions,
		gotermboard.Action{
			Keys: []string{"enter"}, Label: "complete the row, or log the minutes it offers", Footer: "enter done",
			Run: b.complete, Applies: onRow,
		},
		gotermboard.Action{
			Keys: []string{"+", "="}, Label: "offer five more minutes", Footer: "+/- minutes",
			Run: func(m *gotermboard.Model) tea.Cmd { return b.adjust(m, func(o minutesOffer) *panelRow { return o.up }) }, Applies: offering,
		},
		gotermboard.Action{
			Keys: []string{"-"}, Label: "offer five fewer minutes",
			Run: func(m *gotermboard.Model) tea.Cmd {
				return b.adjust(m, func(o minutesOffer) *panelRow { return o.down })
			}, Applies: offering,
		},
	)
}

// answerKey is the key of the answer row with this value, while a nudge is ready to take it.
func (b *nowBoard) answerKey(value string) (string, bool) {
	prefix := kindAnswer + keySeparator + value + keySeparator
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && row.Kind == kindAnswer && strings.HasPrefix(*row.Key, prefix) {
			return *row.Key, true
		}
	}
	return "", false
}

func (b *nowBoard) rowAt(key string) (panelRow, bool) {
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && *row.Key == key && row.Kind != kindAnswer {
			return row, true
		}
	}
	return panelRow{}, false
}

// complete picks the cursor's row, or logs the minutes a pick on it opened.
func (b *nowBoard) complete(m *gotermboard.Model) tea.Cmd {
	key := m.Cursor()
	row, _ := b.rowAt(key)
	if offer, open := b.offers[key]; open {
		return b.pick(key, *offer.log.Key, "logging "+offer.log.Text)
	}
	return b.pick(key, key, "completing "+row.Text)
}

func (b *nowBoard) adjust(m *gotermboard.Model, side func(minutesOffer) *panelRow) tea.Cmd {
	key := m.Cursor()
	if to := side(b.offers[key]); to != nil {
		return b.pick(key, *to.Key, "")
	}
	return nil
}

// pick hands key to `doit desk-nudge panel pick` in the background, for the row it acts for.
func (b *nowBoard) pick(row, key, doing string) tea.Cmd {
	if b.underWay != "" {
		return nil
	}
	b.underWay = doing
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), pickWithin)
		defer cancel()
		step, err := b.panel.pick(ctx, key)
		return pickedMsg{row: row, step: step, err: err}
	}
}

func (b *nowBoard) Receive(m *gotermboard.Model, msg tea.Msg) tea.Cmd {
	picked, ok := msg.(pickedMsg)
	if !ok {
		return nil
	}
	b.underWay = ""
	switch {
	case picked.err != nil:
		delete(b.offers, picked.row)
		m.Say(picked.err.Error())
		b.today.Force()
	case picked.step.Step == stepMinutes:
		if offer, ok := offerFrom(picked.step); ok {
			b.offers = map[string]minutesOffer{picked.row: offer}
		}
	case picked.step.Step == stepDone:
		delete(b.offers, picked.row)
		if picked.step.Message != nil {
			m.Say(*picked.step.Message)
		}
		b.today.Force()
	}
	m.Rebuild()
	return nil
}
