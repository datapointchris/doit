package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"syscall"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/datapointchris/gotermboard"

	"github.com/datapointchris/doit/cli/internal/desknudge"
)

// panelStep is one step of `doit desk-nudge panel` as the board draws it: the rows the desk nudge
// lists for the step doit printed.
type panelStep struct {
	Step    string
	Rows    []panelRow
	Message *string
}

type panelRow = desknudge.Row

// The steps and row kinds the now tab acts on, as doit's panel names them.
const (
	stepMinutes    = desknudge.StepMinutes
	stepDone       = desknudge.StepDone
	kindAnswer     = desknudge.KindAnswer
	kindLog        = desknudge.KindLogMinutes
	kindAdjust     = desknudge.KindAdjustMinutes
	panelReadEvery = 15 * time.Second
	// readWithin bounds one read of the rows, which doit takes from this desk's cache.
	readWithin = 30 * time.Second
	// pickWithin bounds one pick. doit bounds each of its requests itself, and this sits well past
	// those bounds, so doit reports its own failures. Only a completion that never ends reaches it.
	pickWithin = 10 * time.Minute
	// stopWithin is how long a stopped doit, and everything it started, has to exit.
	stopWithin = 5 * time.Second
)

// panel is how the now tab reaches doit, which holds every rule about what a row is and what a
// pick does.
type panel interface {
	show(ctx context.Context) (panelStep, error)
	pick(ctx context.Context, key string) (panelStep, error)
}

// doitPanel runs `doit desk-nudge panel show --json` and `doit desk-nudge panel pick <key> --json`.
type doitPanel struct{}

func (doitPanel) show(ctx context.Context) (panelStep, error) {
	ctx, cancel := context.WithTimeout(ctx, readWithin)
	defer cancel()
	return runPanel(ctx, "show", "--json")
}

func (doitPanel) pick(ctx context.Context, key string) (panelStep, error) {
	return runPanel(ctx, "pick", key, "--json")
}

func runPanel(ctx context.Context, args ...string) (panelStep, error) {
	cmd := exec.CommandContext(ctx, "doit", append([]string{"desk-nudge", "panel"}, args...)...)
	// A session of its own keeps a completion that opens /dev/tty off the board's keys, and lets a
	// stop reach everything doit started.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
	cmd.WaitDelay = stopWithin
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if ctxErr := ctx.Err(); ctxErr != nil {
		return panelStep{}, fmt.Errorf("doit desk-nudge panel %s was stopped: %w", args[0], ctxErr)
	}
	if err != nil {
		return panelStep{}, errors.New(gotermboard.LastLine(stderr.Bytes(), err))
	}
	var printed desknudge.Panel
	if err := json.Unmarshal(out, &printed); err != nil {
		return panelStep{}, errors.New("doit desk-nudge panel printed no step: " + err.Error())
	}
	// doit and doit-tui are released and upgraded apart, so a panel of another version is refused.
	if printed.SchemaVersion != desknudge.PanelSchemaVersion {
		older := "doit"
		if printed.SchemaVersion > desknudge.PanelSchemaVersion {
			older = "doit-tui"
		}
		return panelStep{}, fmt.Errorf("doit desk-nudge panel printed schema_version %d and this doit-tui reads %d, so upgrade %s",
			printed.SchemaVersion, desknudge.PanelSchemaVersion, older)
	}
	return panelStep{Step: printed.Step, Rows: printed.Rows(), Message: printed.Message}, nil
}

// minutesOffer is a timed pursuit's minutes step: the amount enter logs, and the rows that reopen
// the step above and below it.
type minutesOffer struct {
	log      panelRow
	up, down *panelRow
}

func offerFrom(step panelStep) (minutesOffer, bool) {
	var offer minutesOffer
	found := false
	for _, row := range step.Rows {
		if row.Key != nil && row.Kind == kindLog && (row.Suggested || !found) {
			offer.log, found = row, true
		}
	}
	if !found || offer.log.Minutes == nil {
		return minutesOffer{}, found
	}
	for _, row := range step.Rows {
		switch {
		case row.Key == nil || row.Kind != kindAdjust || row.Minutes == nil:
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
	step panelStep
	err  error
}

// nowBoard is the now tab: the suggestion, the answers while a nudge is ready, and every row of
// today that something completes, each completed by a click or a key.
type nowBoard struct {
	panel panel
	today *gotermboard.Source[panelStep]
	// offered is the row whose minutes are open under it, and offer is those minutes. Empty while
	// no row's are.
	offered  string
	offer    minutesOffer
	underWay string
}

func newNowBoard(p panel) *nowBoard {
	b := &nowBoard{panel: p}
	b.today = gotermboard.NewSource("doit desk-nudge panel", panelReadEvery, func(ctx context.Context) (panelStep, string, error) {
		step, err := p.show(ctx)
		return step, "", err
	})
	return b
}

func (b *nowBoard) Sources() []gotermboard.Refresher { return []gotermboard.Refresher{b.today} }
func (b *nowBoard) Groupings() []string              { return nil }
func (b *nowBoard) UnderWay() string                 { return b.underWay }

// Frame draws the suggestion first, then each completion row under the group doit names for it,
// in the order doit lists them.
func (b *nowBoard) Frame(now time.Time, _ string) gotermboard.Frame {
	strip := []gotermboard.Line{gotermboard.ReadStatus([]gotermboard.Status{b.today.Status()}, now)}
	suggested := gotermboard.Group{Key: "suggested", Title: title("up next")}
	var groups []gotermboard.Group
	at := map[string]int{}
	for _, row := range b.today.Value().Rows {
		switch {
		case row.Key == nil:
			strip = append(strip, gotermboard.Line{gotermboard.C(row.Text, gotermboard.Yellow)})
		case row.Kind == kindAnswer:
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
func (b *nowBoard) row(row panelRow) gotermboard.Row {
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

// Actions puts each of a ready nudge's answers on a number, in the order doit lists them.
func (b *nowBoard) Actions() []gotermboard.Action {
	var actions []gotermboard.Action
	for i, answer := range b.answers() {
		number := strconv.Itoa(i + 1)
		actions = append(actions, gotermboard.Action{
			Keys: []string{number}, Label: "answer the nudge: " + answer.Text, Footer: number + " " + answer.Text,
			Run: func(m *gotermboard.Model) tea.Cmd { return b.pick(m.Cursor(), *answer.Key, "answering "+answer.Text) },
		})
	}
	onRow := func(m *gotermboard.Model) bool { _, ok := b.rowAt(m.Cursor()); return ok }
	offering := func(m *gotermboard.Model) bool { return b.offered != "" && m.Cursor() == b.offered }
	return append(actions,
		gotermboard.Action{
			Keys: []string{"enter"}, Label: "complete the row, or log the minutes it offers", Footer: "enter done",
			Run: b.complete, Applies: onRow,
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

func (b *nowBoard) answers() []panelRow {
	var found []panelRow
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && row.Kind == kindAnswer {
			found = append(found, row)
		}
	}
	return found
}

func (b *nowBoard) rowAt(key string) (panelRow, bool) {
	for _, row := range b.today.Value().Rows {
		if row.Key != nil && *row.Key == key && row.Kind != kindAnswer {
			return row, true
		}
	}
	return panelRow{}, false
}

// complete picks the cursor's row, or logs the minutes a pick on it opened. While another row's
// minutes are open, the line under that row says enter logs them, so enter closes them and
// completes nothing.
func (b *nowBoard) complete(m *gotermboard.Model) tea.Cmd {
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

func (b *nowBoard) adjust(to *panelRow) tea.Cmd {
	if to == nil {
		return nil
	}
	return b.pick(b.offered, *to.Key, "")
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
		if errors.Is(err, context.DeadlineExceeded) {
			err = fmt.Errorf("the pick ran past %v and was stopped. It may have landed, so the rows are read again", pickWithin)
		}
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
		b.offered = ""
		m.Say(picked.err.Error())
		b.today.Force()
	case picked.step.Step == stepMinutes:
		if offer, ok := offerFrom(picked.step); ok {
			b.offered, b.offer = picked.row, offer
		}
	case picked.step.Step == stepDone:
		b.offered = ""
		if picked.step.Message != nil {
			m.Say(*picked.step.Message)
		}
		b.today.Force()
	}
	m.Rebuild()
	return nil
}
