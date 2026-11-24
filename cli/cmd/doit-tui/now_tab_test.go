package main

import (
	"context"
	"slices"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"
	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"
)

func key(k string) *string { return &k }

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

func TestTheNowTabGroupsRowsAndCompletesATimedPursuitThroughItsMinutes(t *testing.T) {
	logged := "logged 50 min of read"
	fake := &fakePanel{
		today: panelStep{Step: "today", Rows: []panelRow{
			{Key: nil, Kind: "note", Text: "rows from 14:05 — doit unreachable"},
			{Key: key("pursuit:read"), Kind: "pursuit", Text: "read · A Neutral Book", Suggested: true},
			{Key: key("answer:too_soon:n1"), Kind: "answer", Text: "too soon"},
			{Key: key("habit:17"), Kind: "habit", Text: "habit · Stretch"},
		}},
		picks: map[string]panelStep{
			"pursuit:read": {Step: "minutes", Rows: []panelRow{
				{Key: key("log_minutes:45:pursuit:read"), Kind: "log_minutes", Text: "read · log 45 min (usual)"},
				{Key: key("adjust_minutes:50:pursuit:read"), Kind: "adjust_minutes", Text: "read · +5 → 50 min"},
				{Key: key("adjust_minutes:40:pursuit:read"), Kind: "adjust_minutes", Text: "read · −5 → 40 min"},
			}},
			"adjust_minutes:50:pursuit:read": {Step: "minutes", Rows: []panelRow{
				{Key: key("log_minutes:50:pursuit:read"), Kind: "log_minutes", Text: "read · log 50 min"},
				{Key: key("adjust_minutes:55:pursuit:read"), Kind: "adjust_minutes", Text: "read · +5 → 55 min"},
				{Key: key("adjust_minutes:45:pursuit:read"), Kind: "adjust_minutes", Text: "read · −5 → 45 min"},
			}},
			"log_minutes:50:pursuit:read": {Step: "done", Message: &logged},
		},
	}
	board := newNowBoard(fake)
	gotermboard.RefreshAll(context.Background(), board.Sources()...)
	m := gotermboard.NewModel(board, gotermstyle.NewPalette(false))
	m.Update(tea.WindowSizeMsg{Width: 100, Height: 20})
	screen := func() string { return ansi.Strip(m.View().Content) }
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

	for _, want := range []string{"up next", "A Neutral Book", "habits", "Stretch", "doit unreachable"} {
		if !strings.Contains(screen(), want) {
			t.Errorf("the now tab should draw %q:\n%s", want, screen())
		}
	}
	if strings.Contains(screen(), "habit · ") {
		t.Errorf("a habit row should drop the kind its group already names:\n%s", screen())
	}
	if !slices.ContainsFunc(board.Actions(), func(a gotermboard.Action) bool { return a.Footer == "2 too soon" && a.Applies(m) }) {
		t.Errorf("a ready nudge's answers should be keys with footer hints")
	}

	if !m.MoveCursorTo("pursuit:read") {
		t.Fatal("the suggestion's row should take the cursor")
	}
	press("enter")
	if !strings.Contains(screen(), "log 45 min (usual)") {
		t.Errorf("a timed pursuit's pick should open its minutes under its row:\n%s", screen())
	}
	press("+")
	press("enter")
	if want := []string{"pursuit:read", "adjust_minutes:50:pursuit:read", "log_minutes:50:pursuit:read"}; !slices.Equal(fake.picked, want) {
		t.Errorf("enter, + and enter should open, move and log the minutes: %v", fake.picked)
	}
	if !strings.Contains(screen(), logged) || strings.Contains(screen(), "log 50 min ") {
		t.Errorf("a done step should say what it did and close the minutes:\n%s", screen())
	}
}
