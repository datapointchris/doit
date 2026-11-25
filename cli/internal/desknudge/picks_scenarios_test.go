package desknudge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/datapointchris/doit/cli/internal/service"
)

// askSocket sends one request to the watch's socket, and decodes its answer into answered.
func (f *fakeDesk) askSocket(method, path string, body any, answered any) int {
	var sent bytes.Buffer
	if body != nil {
		_ = json.NewEncoder(&sent).Encode(body)
	}
	request, err := http.NewRequestWithContext(context.Background(), method, "http://doit-watch"+path, &sent)
	if err != nil {
		f.t.Error(err)
		return 0
	}
	response, err := socketClient(f.desk.Paths.Socket).Do(request)
	if err != nil {
		f.t.Error(err)
		return 0
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

// shownPick is the latest pick of key that the panel shows.
func (f *fakeDesk) shownPick(key string) ShownPick {
	f.t.Helper()
	picks := f.desk.Show().Picks
	for _, pick := range slices.Backward(picks) {
		if pick.Key == key {
			return pick
		}
	}
	f.t.Fatalf("the panel shows no pick of %s among %+v", key, picks)
	return ShownPick{}
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

// picked picks key as the page does, and answers the pick's error once everything it began has ended.
func (f *fakeDesk) picked(watch *Watch, key string, minutes *int) error {
	_, err := watch.Pick(context.Background(), key, minutes, false)
	watch.Wait()
	return err
}

// waitFor waits until holds, on the real clock, because a pick runs in a goroutine of its own.
func (f *fakeDesk) waitFor(what string, holds func() bool) {
	f.t.Helper()
	for deadline := time.Now().Add(5 * time.Second); !holds(); time.Sleep(10 * time.Millisecond) {
		if time.Now().After(deadline) {
			f.t.Fatalf("%s never happened", what)
		}
	}
}

func (f *fakeDesk) running(key string) bool {
	return slices.ContainsFunc(f.state().RunningPicks, func(pick RunningPick) bool { return pick.Key == key })
}

// waiting is whether a pick of key has taken its place in the turns.
func (t *pickTurns) waiting(key string) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.keys[key]
}

func shortenHabitBound(t *testing.T, bound time.Duration) {
	was := habitBound
	habitBound = bound
	t.Cleanup(func() { habitBound = was })
}

func TestAClickOnATickRowCommitsInItsOwnRequestAndASecondClickWhileItRunsCommitsNothing(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	f.serving(watch)
	f.lifeHolds("tick")

	answered := make(chan int, 1)
	var step Panel
	go func() { answered <- f.askSocket(http.MethodPost, "/picks", map[string]any{"key": "tick:1"}, &step) }()
	f.waitFor("the tick running", func() bool { return f.running("tick:1") })
	if picks := f.rowOfKey("tick:1").Picks; len(picks) != 1 || picks[0].State != PickCommitting {
		t.Errorf("the squat row shows %+v while its tick runs, want it committing", picks)
	}
	var refusal PickError
	if code := f.askSocket(http.MethodPost, "/picks", map[string]any{"key": "tick:1"}, &refusal); code != http.StatusConflict || refusal.ExitCode != PickRefused {
		t.Errorf("a second click while the tick runs answered %d %+v, want 409 refused", code, refusal)
	}

	f.releaseLife()
	if code := <-answered; code != http.StatusOK || step.Step != StepDone {
		t.Errorf("the click answered %d %+v, want 200 with a done step", code, step)
	}
	watch.Wait()
	if ran := f.ran("life"); !slices.EqualFunc(ran, [][]string{tickProgram("1", f.clockNow().Format(dayLayout)).Argv}, slices.Equal) {
		t.Errorf("life ran %q, want one set ticked", ran)
	}
	if picks := f.rowOfKey("tick:1").Picks; len(picks) != 1 || picks[0].State != OutcomeDone {
		t.Errorf("the squat row shows %+v, want its tick done in place", picks)
	}
	if left := f.state().RunningPicks; len(left) != 0 {
		t.Errorf("the state still holds %+v running, want none", left)
	}
}

func TestTicksAndUnticksRunInClickOrder(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	f.lifeHolds("tick")
	for _, key := range []string{"tick:1", "untick:1", "tick:2"} {
		go func() { _, _ = watch.Pick(context.Background(), key, nil, false) }()
		f.waitFor(key+" taking its turn", func() bool { return watch.turns.waiting(key) })
	}
	f.releaseLife()
	watch.Wait()

	day := f.clockNow().Format(dayLayout)
	want := [][]string{tickProgram("1", day).Argv, untickProgram("1", day).Argv, tickProgram("2", day).Argv}
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

func TestAHabitDoneElsewhereBeforeItsPickIsSkippedAndItsRowSaysSo(t *testing.T) {
	f := newFakeDesk(t)
	f.habitsDoneToday(1)
	watch := f.started()
	var refusal *PickError
	if err := f.picked(watch, "habit:1", nil); !errors.As(err, &refusal) || refusal.ExitCode != PickRefused {
		t.Errorf("picking a habit done elsewhere answered %v, want it refused", err)
	}

	if ran := f.ran("life"); !slices.EqualFunc(ran, [][]string{habitsCompletedProgram(f.clockNow().Format(dayLayout)).Argv}, slices.Equal) {
		t.Errorf("life ran %q, want only the day's completions read again", ran)
	}
	if shown := f.shownPick("habit:1"); shown.State != OutcomeSkipped {
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
	var failure *PickError
	if err := f.picked(watch, "habit:1", nil); !errors.As(err, &failure) || failure.ExitCode != PickFailed {
		t.Errorf("a completion that ran out of time answered %v, want it failed", err)
	}
	if shown := f.shownPick("habit:1"); shown.State != OutcomeUncertain {
		t.Errorf("a completion that ran out of time shows %+v, want it uncertain", shown)
	}
	var refusal *PickError
	if err := f.picked(watch, "habit:1", nil); !errors.As(err, &refusal) || refusal.ExitCode != PickRefused {
		t.Errorf("picking the uncertain row again answered %v, want it refused until the view is read again", err)
	}

	f.releaseLife()
	f.lifeExits("complete", 1)
	_ = f.picked(watch, "habit:3", nil)
	if shown := f.shownPick("habit:3"); shown.State != OutcomeRefused || shown.Message == nil || *shown.Message == "" {
		t.Errorf("a completion life refused shows %+v, want it refused with life's reason", shown)
	}
	_ = f.picked(watch, "habit:3", nil)
	day := f.clockNow().Format(dayLayout)
	completions := slices.DeleteFunc(f.ran("life"), func(argv []string) bool { return !slices.Equal(argv, habitProgram("3", day).Argv) })
	if len(completions) != 2 {
		t.Errorf("life completed habit 3 %d times, want twice, because a refusal frees its row", len(completions))
	}
	if picks := f.rowOfKey("habit:3").Picks; len(picks) != 1 {
		t.Errorf("habit 3's row shows %+v, want only the newest pick, which supersedes the first refusal", picks)
	}
}

func TestAfterARestartAStartedTickIsRecordedUncertainAndNotRerun(t *testing.T) {
	f := newFakeDesk(t)
	f.started()
	picked := f.clockNow()
	panel := f.desk.Show()
	_ = f.desk.Paths.change(func(state *State) error {
		for _, key := range []string{"tick:1", "habit:1"} {
			found, _ := panel.find(key)
			state.RunningPicks = append(state.RunningPicks, RunningPick{
				ID: service.NewWriteID(picked), Key: key, Text: found.text, Action: found.actionOf(), Completes: found.completes,
				PickedAt: stamp(picked), StartedAt: stamp(picked),
			})
		}
		return nil
	})

	// The watch died inside both picks and starts again the next day.
	f.advance(24 * time.Hour)
	f.started()
	day := picked.Format(dayLayout)
	if ran := f.ran("life"); !slices.EqualFunc(ran, [][]string{habitsCompletedProgram(day).Argv, habitProgram("1", day).Argv}, slices.Equal) {
		t.Errorf("life ran %q, want the started habit read again and completed on the day it was picked, and the tick never run again", ran)
	}
	outcomes := map[string]any{}
	for _, pick := range f.picks() {
		outcomes[pick["action"].(string)] = pick["outcome"]
	}
	if outcomes[ActionTick] != OutcomeUncertain || outcomes[ActionComplete] != OutcomeDone {
		t.Errorf("the picked lines read %v, want the tick uncertain and the habit done", f.picks())
	}
	if left := f.state().RunningPicks; len(left) != 0 {
		t.Errorf("the state still holds %+v running, want every pick ended", left)
	}
}

func TestAnExitWaitsForARunningPickInsideItsDeadline(t *testing.T) {
	f := newFakeDesk(t)
	watch := NewWatch(f.desk)
	watch.drainFor = 300 * time.Millisecond
	ctx, stop := context.WithCancel(t.Context())
	exited := make(chan error, 1)
	go func() { exited <- watch.Run(ctx) }()
	f.waitFor("the view read", func() bool { return f.desk.Paths.readPanel() != nil })
	f.lifeHolds("complete")
	go func() { _, _ = watch.Pick(context.Background(), "habit:1", nil, false) }()
	f.waitFor("habit 1 running", func() bool { return f.running("habit:1") })

	began := time.Now()
	stop()
	if err := <-exited; err != nil {
		t.Fatal(err)
	}
	if took := time.Since(began); took > time.Second {
		t.Errorf("the watch took %s to exit, want it inside its 300ms drain", took)
	}
	if left := f.state().RunningPicks; len(left) != 1 || left[0].Key != "habit:1" || left[0].StartedAt == "" {
		t.Errorf("the state holds %+v, want habit 1 marked started, for the next watch's restart rules", left)
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
		held     bool
	}{
		{key: "habit:1", held: true, programs: [][]string{habitsCompletedProgram(day).Argv, habitProgram("1", day).Argv}},
		{key: "tick:1", programs: [][]string{tickProgram("1", day).Argv}},
		{key: "untick:2", programs: [][]string{untickProgram("2", day).Argv}},
		{key: "pursuit:tasks:532", programs: [][]string{{"tick-task", "532", `File the "receipts" now`}}, journal: true, held: true},
		{key: "pursuit:read", minutes: &thirty, journal: true, held: true},
		{key: "review:prune-images", reviewed: "prune-images 2026-11-24", held: true},
		{key: "practice:lab-regex", reviewed: "lab-regex 2026-11-24", held: true},
	} {
		t.Run(row.key, func(t *testing.T) {
			f := newFakeDesk(t)
			if err := f.picked(f.started(), row.key, row.minutes); err != nil {
				t.Fatalf("the pick answered %v, want it done", err)
			}
			shown := f.shownPick(row.key)
			if shown.State != OutcomeDone {
				t.Errorf("the pick shows %+v, want it done", shown)
			}
			ran := append(f.ran("life"), f.ran("tick-task")...)
			if !slices.EqualFunc(ran, row.programs, slices.Equal) {
				t.Errorf("the pick ran %q, want %q", ran, row.programs)
			}
			_, logs := f.posted()
			if row.journal != (len(logs) == 1) || row.journal && logs[0]["id"] != shown.ID {
				t.Errorf("the journal took %v, want one entry under the pick's id %s: %v", logs, shown.ID, row.journal)
			}
			if reviewed := slices.Clone(f.reviews); row.reviewed != "" != (len(reviewed) == 1) || row.reviewed != "" && reviewed[0] != row.reviewed {
				t.Errorf("the service marked done %v, want %q", reviewed, row.reviewed)
			}
			// doit-tui draws panel show's rows and never their picks, so a row whose key the pick holds leaves them.
			if row.held && slices.ContainsFunc(f.desk.Show().Rows, func(listed Row) bool { return listed.Key != nil && *listed.Key == row.key }) {
				t.Errorf("panel show still lists %s, which doit-tui would draw as a row to pick", row.key)
			}
		})
	}
}
