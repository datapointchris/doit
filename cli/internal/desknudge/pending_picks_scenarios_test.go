package desknudge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
)

// askSocket sends one request to the watch's socket, and decodes its answer into answered.
func (f *fakeDesk) askSocket(method, path string, body any, answered any) int {
	f.t.Helper()
	var sent bytes.Buffer
	if body != nil {
		_ = json.NewEncoder(&sent).Encode(body)
	}
	request, err := http.NewRequestWithContext(f.t.Context(), method, "http://doit-watch"+path, &sent)
	if err != nil {
		f.t.Fatal(err)
	}
	response, err := socketClient(f.desk.Paths.Socket).Do(request)
	if err != nil {
		f.t.Fatal(err)
	}
	defer func() { _ = response.Body.Close() }()
	_ = json.NewDecoder(response.Body).Decode(answered)
	return response.StatusCode
}

func (f *fakeDesk) serving(watch *Watch) {
	served, err := watch.serve(f.t.Context())
	if err != nil {
		f.t.Fatal(err)
	}
	f.t.Cleanup(func() { served.stop(context.Background()) })
}

// rowOfKey is today's row that key picks, as panel show prints it.
func (f *fakeDesk) rowOfKey(key string) Row {
	f.t.Helper()
	for _, row := range f.desk.Show().Rows {
		if row.Key != nil && *row.Key == key {
			return row
		}
	}
	f.t.Fatalf("today's rows hold no %s", key)
	return Row{}
}

// picks are the pick field of each picked line, in order.
func (f *fakeDesk) picks() []map[string]any {
	var found []map[string]any
	for _, record := range f.records() {
		if record["event"] == eventPicked {
			found = append(found, record["pick"].(map[string]any))
		}
	}
	return found
}

func (f *fakeDesk) committed(key string, minutes *int) ShownPick {
	f.t.Helper()
	return f.committedAfter(key, minutes, nil)
}

// committedAfter clicks key, then lets its 4 seconds pass, and answers how its row shows the pick.
func (f *fakeDesk) committedAfter(key string, minutes *int, watch *Watch) ShownPick {
	f.t.Helper()
	if watch == nil {
		watch = f.started()
	}
	made, err := watch.CreatePending(key, minutes)
	if err != nil {
		f.t.Fatalf("clicking %s answered %v", key, err)
	}
	f.advance(pendingFor)
	f.fireDue(watch)
	for _, pick := range f.desk.Show().Picks {
		if pick.ID == made.ID {
			return pick
		}
	}
	f.t.Fatalf("the panel no longer shows the pick of %s", key)
	return ShownPick{}
}

func shortenHabitBound(t *testing.T, bound time.Duration) {
	was := habitBound
	habitBound = bound
	t.Cleanup(func() { habitBound = was })
}

func TestAHabitClickCommitsAfterFourSecondsOnceAndASecondClickCommitsNothing(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	f.serving(watch)
	clicked := f.clockNow()

	var made ShownPick
	if code := f.askSocket(http.MethodPost, "/pending-picks", map[string]any{"key": "habit:1"}, &made); code != http.StatusCreated || made.State != PickPending {
		t.Fatalf("the click answered %d %+v, want 201 with the pick pending", code, made)
	}
	if made.CommitAt != stamp(clicked.Add(4*time.Second)) {
		t.Errorf("the pick commits at %s, want 4 seconds after the click", made.CommitAt)
	}
	f.advance(3 * time.Second)
	f.fireDue(watch)
	if ran := f.ran("life"); len(ran) != 0 {
		t.Fatalf("life ran %q a second before the pick's commit_at", ran)
	}
	f.advance(time.Second)
	f.fireDue(watch)
	f.advance(time.Minute)
	f.fireDue(watch)
	day := clicked.Format(dayLayout)
	want := [][]string{habitsCompletedProgram(day).argv, habitProgram("1", day).argv}
	if ran := f.ran("life"); !slices.EqualFunc(ran, want, slices.Equal) {
		t.Errorf("life ran %q, want the day's completions read again, then habit 1 completed once", ran)
	}
	if picks := f.rowOfKey("habit:1").Picks; len(picks) != 1 || picks[0].State != OutcomeDone {
		t.Errorf("habit 1's row shows %+v, want its pick done in place", picks)
	}

	var waiting, canceled ShownPick
	f.askSocket(http.MethodPost, "/pending-picks", map[string]any{"key": "habit:3"}, &waiting)
	code := f.askSocket(http.MethodPost, "/pending-picks", map[string]any{"key": "habit:3"}, &canceled)
	if code != http.StatusOK || canceled.State != OutcomeCanceled || canceled.ID != waiting.ID {
		t.Errorf("a second click answered %d %+v, want 200 with the first click's pick canceled", code, canceled)
	}
	// A double-pressed undo sends the DELETE twice, and the second reads as the first.
	var deleted, again ShownPick
	f.askSocket(http.MethodPost, "/pending-picks", map[string]any{"key": "habit:4"}, &waiting)
	if code := f.askSocket(http.MethodDelete, "/pending-picks/"+waiting.ID, nil, &deleted); code != http.StatusOK || deleted.State != OutcomeCanceled {
		t.Errorf("deleting the pending pick answered %d %+v, want 200 with it canceled", code, deleted)
	}
	if code := f.askSocket(http.MethodDelete, "/pending-picks/"+waiting.ID, nil, &again); code != http.StatusOK || !reflect.DeepEqual(again, deleted) {
		t.Errorf("deleting it again answered %d %+v, want 200 with the same canceled pick", code, again)
	}
	var unknown PickError
	if code := f.askSocket(http.MethodDelete, "/pending-picks/"+newID(clicked), nil, &unknown); code != http.StatusNotFound {
		t.Errorf("deleting an id this desk never held answered %d %+v, want 404", code, unknown)
	}
	f.advance(time.Minute)
	f.fireDue(watch)
	if ran := f.ran("life"); len(ran) != 2 {
		t.Errorf("life ran %q, want nothing for the picks taken back", ran)
	}
	var actions []string
	for _, pick := range f.picks() {
		actions = append(actions, pick["action"].(string))
	}
	if !slices.Equal(actions, []string{ActionComplete, ActionCancel, ActionCancel}) {
		t.Errorf("the picked lines name %v, want habit 1 completed, then two cancels", actions)
	}
}

func TestTicksAndUnticksRunInClickOrder(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	for _, key := range []string{"tick:1", "untick:1", "tick:2"} {
		if _, err := watch.CreatePending(key, nil); err != nil {
			t.Fatalf("clicking %s answered %v", key, err)
		}
		f.advance(time.Second)
	}
	f.advance(pendingFor)
	f.fireDue(watch)

	day := f.clockNow().Format(dayLayout)
	want := [][]string{tickProgram("1", day).argv, untickProgram("1", day).argv, tickProgram("2", day).argv}
	if ran := f.ran("life"); !slices.EqualFunc(ran, want, slices.Equal) {
		t.Errorf("life ran %q, want %q, in click order with no read of the day's completions", ran, want)
	}
	row := f.rowOfKey("tick:1")
	if row.Kind != KindTick || *row.Ticks != 2 || *row.Target != 5 || *row.UntickKey != "untick:1" || len(row.Picks) != 2 {
		t.Errorf("the squat row reads %+v, want a tick row at 2 of 5 sets carrying its tick and its untick", row)
	}
	// doit-tui draws a row's text alone, so the sets ride in it.
	if !strings.HasSuffix(row.Text, "2 of 5 sets") {
		t.Errorf("the squat row's text reads %q, want it to end on its sets", row.Text)
	}
}

func TestAHabitDoneElsewhereBeforeItsCommitIsSkippedAndItsRowSaysSo(t *testing.T) {
	f := newFakeDesk(t)
	f.habitsDoneToday(1)
	watch := f.started()
	shown := f.committedAfter("habit:1", nil, watch)

	if ran := f.ran("life"); !slices.EqualFunc(ran, [][]string{habitsCompletedProgram(f.clockNow().Format(dayLayout)).argv}, slices.Equal) {
		t.Errorf("life ran %q, want only the day's completions read again", ran)
	}
	if shown.State != OutcomeSkipped {
		t.Errorf("habit 1's pick shows %+v, want it skipped", shown)
	}
	if notices := f.ran("notify-send"); len(notices) != 1 {
		t.Errorf("notify-send ran %q, want one notice of the skip", notices)
	}
	if picks := f.picks(); len(picks) != 1 || picks[0]["outcome"] != OutcomeSkipped {
		t.Errorf("the picked lines read %v, want one skipped", picks)
	}
	// The row shows the skip only until a view read begins on a later day.
	f.tick(watch, 24*time.Hour)
	if picks := f.rowOfKey("habit:1").Picks; len(picks) != 0 {
		t.Errorf("habit 1's row shows %+v a day later, want the skip gone", picks)
	}
}

func TestALifeTimeoutKeepsTheRowUncertainAndARefusalFreesIt(t *testing.T) {
	shortenHabitBound(t, 300*time.Millisecond)
	f := newFakeDesk(t)
	watch := f.started()
	f.lifeHolds("complete")
	shown := f.committedAfter("habit:1", nil, watch)
	if shown.State != OutcomeUncertain {
		t.Errorf("a completion that ran out of time shows %+v, want it uncertain", shown)
	}
	var refusal *PickError
	if _, err := watch.CreatePending("habit:1", nil); !errors.As(err, &refusal) || refusal.ExitCode != PickRefused {
		t.Errorf("clicking the uncertain row again answered %v, want it refused until the view is read again", err)
	}

	f.releaseLife()
	f.lifeExits("complete", 1)
	shown = f.committedAfter("habit:3", nil, watch)
	if shown.State != OutcomeRefused || shown.Message == nil || *shown.Message == "" {
		t.Errorf("a completion life refused shows %+v, want it refused with life's reason", shown)
	}
	if again, err := watch.CreatePending("habit:3", nil); err != nil || again.State != PickPending {
		t.Errorf("clicking the refused row again answered %+v, %v; want a new pending pick", again, err)
	}
	if picks := f.rowOfKey("habit:3").Picks; len(picks) != 1 || picks[0].State != PickPending {
		t.Errorf("habit 3's row shows %+v, want only the new click, which supersedes the refusal", picks)
	}
}

func TestAfterARestartAnUnstartedPickCommitsAndAStartedTickIsRecordedUncertainAndNotRerun(t *testing.T) {
	f := newFakeDesk(t)
	first := f.started()
	clicked := f.clockNow().Format(dayLayout)
	for _, key := range []string{"habit:1", "tick:1"} {
		if _, err := first.CreatePending(key, nil); err != nil {
			t.Fatal(err)
		}
	}
	_ = f.desk.Paths.change(func(state *State) error {
		state.waitingOn("tick:1").StartedAt = stampPtr(f.clockNow())
		return nil
	})
	f.forgetTimers()

	// The watch died inside the pick's seconds and starts again the next day.
	f.advance(24 * time.Hour)
	second := f.started()
	if ran := f.ran("life"); len(ran) != 0 {
		t.Errorf("the restarted watch ran %q at its start, want the started tick never run again", ran)
	}
	if picks := f.picks(); len(picks) != 1 || picks[0]["action"] != ActionTick || picks[0]["outcome"] != OutcomeUncertain {
		t.Errorf("the picked lines read %v, want the started tick recorded uncertain", picks)
	}
	f.advance(pendingFor)
	f.fireDue(second)
	if ran := f.ran("life"); !slices.EqualFunc(ran, [][]string{habitsCompletedProgram(clicked).argv, habitProgram("1", clicked).argv}, slices.Equal) {
		t.Errorf("life ran %q, want the unstarted habit pick committed on the day it was clicked", ran)
	}
	if left := f.state().PendingPicks; len(left) != 0 {
		t.Errorf("the state still holds %+v, want every pending pick ended", left)
	}
}

func TestAnExitDrainsInsideItsDeadline(t *testing.T) {
	f := newFakeDesk(t)
	watch := NewWatch(f.desk)
	watch.after = f.after
	watch.drainFor = 300 * time.Millisecond
	ctx, stop := context.WithCancel(t.Context())
	exited := make(chan error, 1)
	go func() { exited <- watch.Run(ctx) }()
	for deadline := time.Now().Add(5 * time.Second); f.desk.Paths.readPanel() == nil; time.Sleep(10 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the watch never read the view")
		}
	}
	f.lifeHolds("complete")
	for _, key := range []string{"tick:1", "habit:1"} {
		if _, err := watch.CreatePending(key, nil); err != nil {
			t.Fatal(err)
		}
	}

	began := time.Now()
	stop()
	if err := <-exited; err != nil {
		t.Fatal(err)
	}
	if took := time.Since(began); took > time.Second {
		t.Errorf("the watch took %s to exit, want it inside its 300ms drain", took)
	}
	day := f.clockNow().Format(dayLayout)
	if ticked := f.ran("life"); !slices.ContainsFunc(ticked, func(argv []string) bool { return slices.Equal(argv, tickProgram("1", day).argv) }) {
		t.Errorf("life ran %q, want the pending tick committed by the drain before its commit_at", ticked)
	}
	if left := f.state().PendingPicks; len(left) != 1 || left[0].Key != "habit:1" || left[0].StartedAt == nil {
		t.Errorf("the state holds %+v, want only habit 1, marked started, for the next watch's restart rules", left)
	}
	f.releaseLife()
	watch.Wait()
}

func TestEachKeyKindCommitsThroughItsCommandAndAnOnLogLabelStaysOneArgument(t *testing.T) {
	thirty := 30
	day := deskClockStart.Format(dayLayout)
	for _, row := range []struct {
		key      string
		minutes  *int
		programs [][]string
		journal  bool
		reviewed string
	}{
		{key: "habit:1", programs: [][]string{habitsCompletedProgram(day).argv, habitProgram("1", day).argv}},
		{key: "tick:1", programs: [][]string{tickProgram("1", day).argv}},
		{key: "untick:2", programs: [][]string{untickProgram("2", day).argv}},
		{key: "pursuit:tasks:532", programs: [][]string{{"tick-task", "532", `File the "receipts" now`}}, journal: true},
		{key: "pursuit:read", minutes: &thirty, journal: true},
		{key: "review:prune-images", reviewed: "prune-images 2026-11-24"},
		{key: "practice:lab-regex", reviewed: "lab-regex 2026-11-24"},
	} {
		t.Run(row.key, func(t *testing.T) {
			f := newFakeDesk(t)
			shown := f.committed(row.key, row.minutes)
			if shown.State != OutcomeDone {
				t.Fatalf("the pick shows %+v, want it done", shown)
			}
			ran := append(f.ran("life"), f.ran("tick-task")...)
			if !slices.EqualFunc(ran, row.programs, slices.Equal) {
				t.Errorf("the commit ran %q, want %q", ran, row.programs)
			}
			_, logs := f.posted()
			if row.journal != (len(logs) == 1) || row.journal && logs[0]["id"] != shown.ID {
				t.Errorf("the journal took %v, want one entry under the pick's id %s: %v", logs, shown.ID, row.journal)
			}
			if reviewed := slices.Clone(f.reviews); row.reviewed != "" != (len(reviewed) == 1) || row.reviewed != "" && reviewed[0] != row.reviewed {
				t.Errorf("the service marked done %v, want %q", reviewed, row.reviewed)
			}
		})
	}
}
