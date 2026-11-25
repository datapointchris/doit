package desknudge

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/datapointchris/doit/cli/internal/service"
)

// holdWatchLockVariable names the lock that this test binary, started again,
// holds as another watch would, because a POSIX lock never refuses its own process.
const holdWatchLockVariable = "DESK_NUDGE_TEST_HOLD_WATCH_LOCK"

func TestMain(m *testing.M) {
	if path := os.Getenv(holdWatchLockVariable); path != "" {
		os.Exit(holdWatchLockUntilStdinCloses(path))
	}
	os.Exit(m.Run())
}

func holdWatchLockUntilStdinCloses(path string) int {
	lock, err := Paths{WatchLock: path}.HoldWatchLock()
	if err != nil {
		_, _ = fmt.Fprintln(os.Stderr, err)
		return 1
	}
	defer func() { _ = lock.Close() }()
	_, _ = fmt.Println("held")
	_, _ = io.Copy(io.Discard, os.Stdin)
	return 0
}

func TestTheNudgeFiresAtTheIntervalChimesOnceAsItTurnsReadyAndAPickNotifies(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	nudge := f.readyNudge(watch)
	f.tick(watch, tickEvery)
	f.tick(watch, tickEvery)

	if suggested := f.state().Nudge.Suggestion; suggested == nil || suggested.Pursuit != "tasks" {
		t.Errorf("the ready nudge suggests %+v, want the view's suggestion, tasks", suggested)
	}
	if chimes := f.ran("canberra-gtk-play"); len(chimes) != 1 || !slices.Equal(chimes[0], chimeProgram().argv) {
		t.Errorf("the chime ran %q, want once as %q", chimes, chimeProgram().argv)
	}
	if draws := f.viewDraws(); !slices.Equal(draws, []bool{false, true}) {
		t.Errorf("the view was read with draws %v, want the start's read without one, then the firing's with one", draws)
	}
	for _, record := range f.records() {
		if record["event"] == eventFired {
			if _, listed := record["sessions"].([]any); !listed || record["focused_window"] == nil {
				t.Errorf("fired carries sessions %v and focused window %v, want what the desk showed", record["sessions"], record["focused_window"])
			}
		}
	}

	ctx := context.Background()
	serving, err := watch.serve(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer serving.stop(ctx)
	step, err := f.desk.Paths.AskPick(ctx, "review:prune-images", nil)
	watch.Wait()
	if err != nil || step.Step != StepDone || step.Message == nil || !strings.HasPrefix(*step.Message, "prune-images (2026-11-24)") {
		t.Fatalf("the pick over the socket answered %+v, %v; want a done step marking prune-images done today", step, err)
	}
	if notices := f.ran("notify-send"); len(notices) != 1 {
		t.Errorf("notify-send ran %q, want one notice of the pick", notices)
	}
	state := f.state()
	if state.Phase != Counting || state.CountedSeconds != 0 || state.Nudge != nil {
		t.Errorf("after the pick the clock is %s at %vs holding %+v, want the nudge ended and the clock restarted", state.Phase, state.CountedSeconds, state.Nudge)
	}
	picked := f.records()[len(f.records())-1]
	if pick, _ := picked["pick"].(map[string]any); picked["event"] != eventPicked || picked["nudge_id"] != nudge.NudgeID || pick["ok"] != true {
		t.Errorf("the last line is %v, want a pick that went through, naming nudge %s", picked, nudge.NudgeID)
	}
	if draws := f.viewDraws(); !slices.Equal(draws, []bool{false, true, true}) {
		t.Errorf("after the pick the view was read with draws %v, want one more read with a draw", draws)
	}
}

func TestAnAnswerWhileTheServiceIsDownIsQueuedThenResent(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	nudge := f.readyNudge(watch)
	ctx := context.Background()

	f.down.Store(true)
	answered, err := f.desk.Answer(ctx, "too_soon", "")
	if err != nil {
		t.Fatal(err)
	}
	if code, said := answered.Outcome(); code != service.NoAnswerExit || !answered.queued {
		t.Errorf("the answer came out %d %q, want exit 4 with the answer kept on this desk", code, said)
	}
	state := f.state()
	if len(state.QueuedAnswers) != 1 || state.QueuedAnswers[0].Row.NudgeID != nudge.NudgeID || state.Phase != Counting {
		t.Fatalf("the state holds %+v in phase %s, want the answer queued and its nudge ended", state.QueuedAnswers, state.Phase)
	}
	if notices := f.ran("notify-send"); len(notices) != 1 {
		t.Errorf("notify-send ran %q, want one notice that the answer is not saved yet", notices)
	}

	f.down.Store(false)
	f.tick(watch, tickEvery)
	stored, _ := f.posted()
	if len(stored) != 1 || stored[0] != state.QueuedAnswers[0].Row {
		t.Errorf("the service stored %+v, want the queued row %+v as it was kept", stored, state.QueuedAnswers[0].Row)
	}
	if left := f.state().QueuedAnswers; len(left) != 0 {
		t.Errorf("the queue still holds %+v once the service stored it", left)
	}
	var lines [][2]any
	for _, record := range f.records() {
		if record["event"] == eventAnswered {
			lines = append(lines, [2]any{record["saved"], record["queued"]})
		}
	}
	if !slices.Equal(lines, [][2]any{{false, true}, {true, false}}) {
		t.Errorf("answered was written as (saved, queued) %v, want queued once, then saved once", lines)
	}
}

func TestAnAbsenceRestartsTheClockAndTheIdleHintNeverMovesIt(t *testing.T) {
	f := newFakeDesk(t)
	f.interval = 10
	watch := f.started()

	f.idle(true)
	for range 10 {
		f.tick(watch, tickCap)
	}
	if counted := f.state().CountedSeconds; counted != 300 {
		t.Errorf("an idle desk counted %vs over ten 30s ticks, want 300s", counted)
	}
	if hints := f.ran("loginctl"); len(hints) != 2 {
		t.Errorf("logind's idle hint was read %d times, want at the start's read and at the next one due", len(hints))
	}
	if draws := f.viewDraws(); !slices.Equal(draws, []bool{false}) {
		t.Errorf("while idle the view was read with draws %v, want the timed read skipped", draws)
	}
	f.idle(false)
	f.tick(watch, tickEvery)
	if draws := f.viewDraws(); !slices.Equal(draws, []bool{false, false}) {
		t.Errorf("back at the desk the view was read with draws %v, want the skipped timed read, with no draw", draws)
	}

	f.advance(2 * time.Hour)
	watch.Tick(context.Background())
	watch.Wait()
	if counted := f.state().CountedSeconds; counted != 345 {
		t.Errorf("two hours away while counting left %vs, want the 315s kept and one tick's 30s added", counted)
	}
	nudge := f.readyNudge(watch)
	f.advance(2 * time.Hour)
	watch.Tick(context.Background())
	watch.Wait()
	state := f.state()
	if state.Phase != Counting || state.CountedSeconds != 0 || state.Nudge != nil {
		t.Errorf("two hours away from ready nudge %s left phase %s at %vs, want it expired and the clock restarted", nudge.NudgeID, state.Phase, state.CountedSeconds)
	}
	events := f.events()
	if !slices.Contains(events, eventGap) || events[len(events)-1] != eventExpired {
		t.Errorf("the events read %v, want each absence written as gap and the ready nudge as expired", events)
	}
}

func TestARestartResumesTheClockFromTheStateFile(t *testing.T) {
	f := newFakeDesk(t)
	f.interval = 10
	first := f.started()
	for range 3 {
		f.tick(first, tickCap)
	}

	f.advance(20 * time.Second)
	second := f.started()
	f.tick(second, tickEvery)
	if counted := f.state().CountedSeconds; counted != 120 {
		t.Errorf("after a 35s stop the clock reads %vs, want 90s kept and the stop counted as one capped tick", counted)
	}
	records := f.records()
	restarted := records[slices.IndexFunc(records[1:], func(r map[string]any) bool { return r["event"] == eventStarted })+1]
	if restarted["counted_minutes"] != 1.5 {
		t.Errorf("the second started line reads %v counted minutes, want the 1.5 the first watch left", restarted["counted_minutes"])
	}

	_ = f.desk.Paths.change(func(state *State) error {
		state.beginFiring(f.clockNow(), "019ab7c2-0003-7aaa-8bbb-000000000003")
		return nil
	})
	f.started()
	state := f.state()
	if state.Phase != Ready || state.Nudge.NudgeID != "019ab7c2-0003-7aaa-8bbb-000000000003" {
		t.Errorf("a watch started over a firing left %s holding %+v, want that nudge finished and ready", state.Phase, state.Nudge)
	}
	if chimes := f.ran("canberra-gtk-play"); len(chimes) != 1 {
		t.Errorf("the finished firing chimed %d times, want once", len(chimes))
	}
}

func TestTheGoWatchStartsFromARecordedPythonStateFileAndKeepsItsClockItsNudgeAndItsQueuedAnswers(t *testing.T) {
	f := newFakeDesk(t)
	f.interval = 45
	if err := os.MkdirAll(filepath.Dir(f.desk.Paths.State), 0o755); err != nil {
		t.Fatal(err)
	}
	f.write(f.desk.Paths.State, string(testdata(t, "python-state.json")))
	recorded, err := parseState(testdata(t, "python-state.json"))
	if err != nil {
		t.Fatal(err)
	}

	watch := f.started()
	f.tick(watch, tickEvery)
	state := f.state()
	if state.Phase != Ready || state.CountedSeconds != recorded.CountedSeconds {
		t.Errorf("the Go watch reads %s at %vs, want the recorded ready nudge at %vs", state.Phase, state.CountedSeconds, recorded.CountedSeconds)
	}
	if state.Nudge.NudgeID != recorded.Nudge.NudgeID || *state.Nudge.ReadyUntil != *recorded.Nudge.ReadyUntil ||
		state.Nudge.Suggestion.Label != "File the receipts\nand the warranty cards" {
		t.Errorf("the nudge in hand is %+v, want the recorded one, %+v", state.Nudge, recorded.Nudge)
	}
	if started := f.records()[0]; started["phase"] != string(Ready) || started["counted_minutes"] != 45.04 {
		t.Errorf("started reads %v, want the recorded phase and minutes", started)
	}
	stored, _ := f.posted()
	if len(stored) != 1 || stored[0] != recorded.QueuedAnswers[0].Row || len(state.QueuedAnswers) != 0 {
		t.Errorf("the service stored %+v and %d stay queued, want the recorded row %+v sent as it was kept", stored, len(state.QueuedAnswers), recorded.QueuedAnswers[0].Row)
	}
	if chimes := f.ran("canberra-gtk-play"); len(chimes) != 0 {
		t.Errorf("the recorded ready nudge chimed %d times, want none, because the Python watch chimed it", len(chimes))
	}
}

func TestASecondWatchRefusesToStart(t *testing.T) {
	f := newFakeDesk(t)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	holder := exec.CommandContext(t.Context(), self)
	holder.Env = append(os.Environ(), holdWatchLockVariable+"="+f.desk.Paths.WatchLock)
	stdin, err := holder.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := holder.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := holder.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = stdin.Close(); _ = holder.Wait() }()
	if line, _ := bufio.NewReader(stdout).ReadString('\n'); line != "held\n" {
		t.Fatalf("the other watch answered %q, want it holding the lock", line)
	}

	err = NewWatch(f.desk).Run(context.Background())
	if !errors.Is(err, ErrAnotherWatch) {
		t.Errorf("a second watch answered %v, want %v", err, ErrAnotherWatch)
	}
	if _, statErr := os.Stat(f.desk.Paths.Socket); !errors.Is(statErr, os.ErrNotExist) {
		t.Errorf("the refused watch left a socket at %s", f.desk.Paths.Socket)
	}
}

func TestStatusPauseResumeAndAnswerWorkWithNoWatchAndPanelShowReadsTheCache(t *testing.T) {
	f := newFakeDesk(t)
	nudge := f.readyNudge(f.started())
	draws := len(f.viewDraws())
	ctx := context.Background()

	f.advance(2 * time.Minute)
	if status := f.desk.Status(); status.Phase != Ready || *status.NudgeID != nudge.NudgeID || status.WatchRunning {
		t.Errorf("status reads %s, nudge %v, watch running %v; want the ready nudge from the file and no watch", status.Phase, status.NudgeID, status.WatchRunning)
	}
	shown := f.desk.Show()
	if shown.Step != StepToday || shown.Nudge == nil || shown.Nudge.NudgeID != nudge.NudgeID ||
		!slices.ContainsFunc(shown.Rows, func(row Row) bool { return row.Key != nil && *row.Key == "review:prune-images" }) {
		t.Errorf("panel show answered step %s, nudge %+v; want today's rows from the cache under the ready nudge", shown.Step, shown.Nudge)
	}
	if len(f.viewDraws()) != draws {
		t.Error("panel show asked the service, want it reading the cache alone")
	}

	if paused, err := f.desk.Pause(); err != nil || !paused || f.state().Phase != Paused {
		t.Errorf("pause answered %v, %v in phase %s; want the clock paused", paused, err, f.state().Phase)
	}
	if resumed, err := f.desk.Resume(); err != nil || !resumed || f.state().Phase != Counting {
		t.Errorf("resume answered %v, %v in phase %s; want the clock counting", resumed, err, f.state().Phase)
	}
	answered, err := f.desk.Answer(ctx, "good_timing", nudge.NudgeID)
	if code, said := answered.Outcome(); err != nil || code != 0 || !answered.saved || !answered.late {
		t.Errorf("a late answer came out %d %q, %v; want it saved and the clock left alone", code, said, err)
	}
	if _, err := f.desk.Paths.AskPick(ctx, "review:prune-images", nil); !errors.Is(err, ErrNoWatch) {
		t.Errorf("a pick with no watch answered %v, want %v", err, ErrNoWatch)
	}
}

func TestEachProgramTheWatchStartsHasItsArgvAndBound(t *testing.T) {
	onLog, err := onLogProgram(`tick-task {id} {label} --minutes={minutes} --for {pursuit}`, map[string]string{
		"id": "532", "label": `File the "receipts" now`, "note": "", "minutes": "30", "pursuit": "tasks",
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range []struct {
		program program
		argv    []string
		bound   time.Duration
	}{
		{claudeSessionsProgram(), []string{"claude-sessions", "--json"}, 5 * time.Second},
		{hyprctlInstancesProgram(), []string{"hyprctl", "instances", "-j"}, 5 * time.Second},
		{hyprctlProgram("instance-1", "activewindow"), []string{"hyprctl", "-i", "instance-1", "activewindow", "-j"}, 5 * time.Second},
		{hyprctlProgram("instance-1", "monitors"), []string{"hyprctl", "-i", "instance-1", "monitors", "-j"}, 5 * time.Second},
		{chimeProgram(), []string{"canberra-gtk-play", "--id", "message-new-instant", "--description", "desk nudge"}, 5 * time.Second},
		{notifyProgram("doit: logged", "Logged read"), []string{"notify-send", "-a", "doit", "doit: logged", "Logged read"}, 5 * time.Second},
		{idleHintProgram(), []string{"loginctl", "show-session", "auto", "-p", "IdleHint", "--value"}, 5 * time.Second},
		{habitProgram("7", "2026-11-24"), []string{"life", "habits", "complete", "7", "--date", "2026-11-24"}, 30 * time.Second},
		{habitsCompletedProgram("2026-11-24"), []string{"life", "habits", "completed", "--since", "2026-11-24", "--until", "2026-11-24", "--json"}, 30 * time.Second},
		{tickProgram("3", "2026-11-24"), []string{"life", "habits", "tick", "3", "--date", "2026-11-24"}, 30 * time.Second},
		{untickProgram("3", "2026-11-24"), []string{"life", "habits", "untick", "3", "--date", "2026-11-24"}, 30 * time.Second},
		{onLog, []string{"tick-task", "532", `File the "receipts" now`, "--minutes=30", "--for", "tasks"}, 30 * time.Second},
	} {
		if !slices.Equal(row.program.argv, row.argv) || row.program.bound != row.bound {
			t.Errorf("%s runs as %q within %s, want %q within %s", row.argv[0], row.program.argv, row.program.bound, row.argv, row.bound)
		}
	}
}
