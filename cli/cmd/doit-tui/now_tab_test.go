package main

import (
	"context"
	"slices"
	"strconv"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"
)

func ref[T any](v T) *T { return &v }

// fakePanel answers as doit's panel does, from steps written as `panel show` and `panel pick` print them.
type fakePanel struct {
	today  panelStep
	picks  map[string]panelStep
	picked []string
}

func (f *fakePanel) show(context.Context) (panelStep, error) { return f.today, nil }

func (f *fakePanel) pick(_ context.Context, k string) (panelStep, error) {
	f.picked = append(f.picked, k)
	return f.picks[k], nil
}

// minutesStep is read's minutes step at log minutes, as doit prints it: the log row, then the rows above and below.
func minutesStep(log, up, down int) panelStep {
	row := func(kind string, n int, suggested bool) panelRow {
		return panelRow{Key: ref(kind + ":" + strconv.Itoa(n) + ":pursuit:read"), Kind: kind, Text: kind, Minutes: ref(n), Suggested: suggested}
	}
	return panelStep{Step: "minutes", Rows: []panelRow{row(kindLog, log, true), row(kindAdjust, up, false), row(kindAdjust, down, false)}}
}

func TestTheNowTabDrawsDoitsGroupsAndActsOnlyOnTheRowItShows(t *testing.T) {
	open, more := minutesStep(45, 50, 40), minutesStep(50, 55, 45)
	fake := &fakePanel{
		today: panelStep{Step: "today", Rows: []panelRow{
			{Kind: "note", Text: "rows from 14:05 — doit unreachable"},
			{Key: ref("pursuit:read"), Kind: "pursuit", Text: "read · A Neutral Book", Group: ref("pursuits"), Suggested: true},
			{Key: ref("answer:too_soon:n1"), Kind: "answer", Text: "too soon"},
			{Key: ref("habit:17"), Kind: "habit", Text: "Stretch", Group: ref("habits")},
			{Key: ref("tick:1"), Kind: "tick", Text: "Exercise · Squat · 2 of 5 sets", Group: ref("habits"), UntickKey: ref("untick:1")},
		}},
		picks: map[string]panelStep{
			"pursuit:read":       open,
			*open.Rows[1].Key:    more,
			*more.Rows[0].Key:    {Step: "done", Message: ref("logged 50 min of read")},
			"habit:17":           {Step: "done", Message: ref("habit completed")},
			"answer:too_soon:n1": {Step: "done"},
			"untick:1":           {Step: "done", Message: ref("set taken back")},
		},
	}
	board := newNowBoard(fake)
	gotermboard.RefreshAll(context.Background(), board.Sources()...)
	m := gotermboard.NewModel(board, gotermstyle.NewPalette(false))
	m.Update(tea.WindowSizeMsg{Width: 100, Height: 20})
	press := func(k string) {
		for _, action := range board.Actions() {
			if slices.Contains(action.Keys, k) && (action.Applies == nil || action.Applies(m)) {
				if cmd := action.Run(m); cmd != nil {
					board.Receive(m, cmd())
				}
				return
			}
		}
		t.Fatalf("no key %q applies on %q", k, m.Cursor())
	}
	on := func(key string) {
		if !m.MoveCursorTo(key) {
			t.Fatalf("no row %q takes the cursor", key)
		}
	}
	minutesUnderRead := func() bool {
		for _, g := range board.Frame(time.Now(), "").Groups {
			for _, row := range g.Rows {
				if row.Key == "pursuit:read" {
					return len(row.Second) > 0
				}
			}
		}
		return false
	}

	frame := board.Frame(time.Now(), "")
	var drawn [][]string
	for _, g := range frame.Groups {
		keys := []string{g.Key}
		for _, row := range g.Rows {
			keys = append(keys, row.Key)
		}
		drawn = append(drawn, keys)
	}
	if want := [][]string{{"suggested", "pursuit:read"}, {"habits", "habit:17", "tick:1"}}; !slices.EqualFunc(drawn, want, slices.Equal) {
		t.Errorf("the suggestion should come first, then each row under the group doit names: %v", drawn)
	}
	if !slices.ContainsFunc(frame.Strip, func(l gotermboard.Line) bool { return l.Text() == "rows from 14:05 — doit unreachable" }) {
		t.Errorf("a note should draw above the table")
	}

	press("1")
	on("pursuit:read")
	press("enter")
	if !minutesUnderRead() {
		t.Errorf("a timed pursuit's pick should open its minutes under its row")
	}
	on("habit:17")
	press("enter")
	if minutesUnderRead() || len(fake.picked) != 2 {
		t.Errorf("enter on another row should close the minutes and complete nothing: %v", fake.picked)
	}
	press("enter")
	on("pursuit:read")
	press("enter")
	press("+")
	press("enter")
	on("tick:1")
	press("u")
	want := []string{"answer:too_soon:n1", "pursuit:read", "habit:17", "pursuit:read", *open.Rows[1].Key, *more.Rows[0].Key, "untick:1"}
	if !slices.Equal(fake.picked, want) {
		t.Errorf("each key should pick only the row the screen shows:\n got %v\nwant %v", fake.picked, want)
	}
	if minutesUnderRead() {
		t.Errorf("a done step should close the minutes")
	}
}
