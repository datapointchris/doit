package desknudge

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/oauth2"

	"github.com/datapointchris/doit/cli/internal/service"
)

// The programs the desk nudge starts, each faked by one script that records its argv.
var fakedPrograms = []string{"claude-sessions", "hyprctl", "canberra-gtk-play", "notify-send", "loginctl", "life", "tick-task"}

// fakeProgram records its argv, one tab-separated line each run, and answers
// from the canned files: claude-sessions, each hyprctl query, and the idle hint.
const fakeProgram = `#!/bin/sh
name=${0##*/}
line=$name
for word in "$@"; do line="$line	$word"; done
printf '%s\n' "$line" >> "$FAKE_LOG"
case "$name" in
claude-sessions) cat "$FAKE_DIR/claude-sessions.json" ;;
hyprctl) if [ "$1" = instances ]; then cat "$FAKE_DIR/hyprctl-instances.json"; else cat "$FAKE_DIR/hyprctl-$3.json"; fi ;;
loginctl) cat "$FAKE_DIR/idle-hint" ;;
esac
if [ -f "$FAKE_DIR/$name.exit" ]; then exit "$(cat "$FAKE_DIR/$name.exit")"; fi
`

// fakeDesk is a desk on fakes: the service, every program, and the clock.
type fakeDesk struct {
	t       *testing.T
	desk    *Desk
	canned  string
	argvLog string
	view    []byte

	clock   sync.Mutex
	now     time.Time
	down    atomic.Bool
	service sync.Mutex
	// interval is what GET /desk-nudge and an answer's reply carry.
	interval float64
	// draws is the draw flag of each read of the view, in order.
	draws []bool
	// answers and logs are each body POSTed to /desk-nudge/answers and /journal/done.
	answers []AnswerRow
	logs    []map[string]any
}

var deskClockStart = time.Date(2026, 11, 24, 18, 20, 0, 0, time.FixedZone("EST", -5*60*60))

func newFakeDesk(t *testing.T) *fakeDesk {
	root := t.TempDir()
	// A unix socket's path holds at most 108 bytes, so the runtime directory is short.
	runtime, err := os.MkdirTemp("", "desknudge")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	f := &fakeDesk{t: t, now: deskClockStart, interval: 1, canned: filepath.Join(root, "canned"), argvLog: filepath.Join(root, "argv.log")}
	bin := filepath.Join(root, "bin")
	for _, dir := range []string{bin, f.canned, filepath.Join(runtime, "hypr", "instance")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	script := filepath.Join(bin, "fake-program")
	f.write(script, fakeProgram)
	if err := os.Chmod(script, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range fakedPrograms {
		if err := os.Symlink(script, filepath.Join(bin, name)); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"claude-sessions.json", "hyprctl-instances.json", "hyprctl-activewindow.json"} {
		f.write(filepath.Join(f.canned, name), string(testdata(t, name)))
	}
	f.write(filepath.Join(f.canned, "hyprctl-monitors.json"), `[{"name": "DP-1", "dpmsStatus": true}]`)
	f.idle(false)
	f.write(filepath.Join(runtime, "hypr", "instance", ".socket.sock"), "")
	shell, err := exec.LookPath("sh")
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+filepath.Dir(shell))
	t.Setenv("FAKE_DIR", f.canned)
	t.Setenv("FAKE_LOG", f.argvLog)
	t.Setenv("XDG_STATE_HOME", filepath.Join(root, "state"))
	t.Setenv("XDG_CACHE_HOME", filepath.Join(root, "cache"))
	t.Setenv("XDG_RUNTIME_DIR", runtime)
	t.Setenv("WAYLAND_DISPLAY", "wayland-1")

	f.view = testdata(t, "desk-panel-view.json")
	served := httptest.NewServer(f.routes())
	t.Cleanup(served.Close)
	f.desk = &Desk{
		Paths: DeskPaths(),
		Now:   f.clockNow,
		Open: func(context.Context) (*service.Client, error) {
			if f.down.Load() {
				return nil, &service.NoAnswer{Message: "no answer from the doit service: connection refused"}
			}
			return service.New(served.URL, oauth2.StaticTokenSource(&oauth2.Token{AccessToken: "t"}), "", "Run `doit auth login`."), nil
		},
		Machine: "desk",
		Actor:   "person@desk",
		Version: "0.0.0-test",
	}
	return f
}

func testdata(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func (f *fakeDesk) write(path, text string) {
	f.t.Helper()
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		f.t.Fatal(err)
	}
}

func (f *fakeDesk) clockNow() time.Time {
	f.clock.Lock()
	defer f.clock.Unlock()
	return f.now
}

func (f *fakeDesk) advance(by time.Duration) {
	f.clock.Lock()
	defer f.clock.Unlock()
	f.now = f.now.Add(by)
}

// idle sets what logind's idle hint answers.
func (f *fakeDesk) idle(idle bool) {
	hint := "no"
	if idle {
		hint = "yes"
	}
	f.write(filepath.Join(f.canned, "idle-hint"), hint+"\n")
}

func (f *fakeDesk) routes() http.Handler {
	answer := func(response http.ResponseWriter, body any) {
		response.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(response).Encode(body)
	}
	interval := func() map[string]any {
		return map[string]any{"interval_minutes": f.interval, "answer_counts": map[string]int{"good_timing": 0, "too_soon": 0, "too_late": 0, "bothered": 0}}
	}
	routes := http.NewServeMux()
	routes.HandleFunc("GET /api/v1/desk-panel-view", func(response http.ResponseWriter, request *http.Request) {
		f.service.Lock()
		f.draws = append(f.draws, request.URL.Query().Get("draw") == "true")
		f.service.Unlock()
		response.Header().Set("Content-Type", "application/json")
		_, _ = response.Write(f.view)
	})
	routes.HandleFunc("GET /api/v1/desk-nudge", func(response http.ResponseWriter, _ *http.Request) {
		f.service.Lock()
		defer f.service.Unlock()
		answer(response, interval())
	})
	routes.HandleFunc("POST /api/v1/desk-nudge/answers", func(response http.ResponseWriter, request *http.Request) {
		var row AnswerRow
		_ = json.NewDecoder(request.Body).Decode(&row)
		f.service.Lock()
		defer f.service.Unlock()
		f.answers = append(f.answers, row)
		stored := interval()
		stored["added"] = true
		answer(response, stored)
	})
	routes.HandleFunc("POST /api/v1/review/{entry}/completions", func(response http.ResponseWriter, request *http.Request) {
		var body struct {
			Day string `json:"day"`
		}
		_ = json.NewDecoder(request.Body).Decode(&body)
		answer(response, map[string]any{"done_on": body.Day, "status": map[string]any{"cadence_days": 7}})
	})
	routes.HandleFunc("GET /api/v1/pursuits/register", func(response http.ResponseWriter, _ *http.Request) {
		answer(response, map[string]any{
			"pursuits": map[string]any{
				"tasks": map[string]any{"resolve": map[string]any{"adapter": "life-tasks"}, "on_log": "tick-task {id} {label}"},
				"read":  map[string]any{"unit": "minutes"},
			},
			"active": []string{"read", "tasks"}, "offered": []string{}, "draw_id": nil,
		})
	})
	routes.HandleFunc("GET /api/v1/pursuits/tasks/item", func(response http.ResponseWriter, _ *http.Request) {
		answer(response, map[string]any{"pursuit": "tasks", "draw_id": nil, "resolved": map[string]any{
			"pursuit": "tasks", "backend": "life", "candidates": 3, "choices": []map[string]any{
				{"id": "532", "label": "File the receipts"},
				{"id": "533", "label": "Return the library books"},
				{"id": "534", "label": "Descale the kettle"},
			},
		}})
	})
	routes.HandleFunc("POST /api/v1/journal/done", func(response http.ResponseWriter, request *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(request.Body).Decode(&body)
		f.service.Lock()
		f.logs = append(f.logs, body)
		f.service.Unlock()
		answer(response, map[string]any{"due_in_days": 2})
	})
	return routes
}

// ran is the argv of each run of program, in order.
func (f *fakeDesk) ran(program string) [][]string {
	f.t.Helper()
	file, err := os.Open(f.argvLog)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		f.t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	var runs [][]string
	lines := bufio.NewScanner(file)
	for lines.Scan() {
		argv := strings.Split(lines.Text(), "\t")
		if argv[0] == program {
			runs = append(runs, argv)
		}
	}
	return runs
}

func (f *fakeDesk) state() *State {
	f.t.Helper()
	state := f.desk.Paths.readState()
	if state == nil {
		f.t.Fatal("the desk has no readable state file")
	}
	return state
}

// records are the lines of the events file, each parsed.
func (f *fakeDesk) records() []map[string]any {
	f.t.Helper()
	file, err := os.Open(f.desk.Paths.Records)
	if err != nil {
		f.t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	var found []map[string]any
	decoder := json.NewDecoder(file)
	for {
		var record map[string]any
		if err := decoder.Decode(&record); err == io.EOF {
			return found
		} else if err != nil {
			f.t.Fatal(err)
		}
		found = append(found, record)
	}
}

func (f *fakeDesk) events() []string {
	var events []string
	for _, record := range f.records() {
		events = append(events, record["event"].(string))
	}
	return events
}

func (f *fakeDesk) viewDraws() []bool {
	f.service.Lock()
	defer f.service.Unlock()
	return append([]bool(nil), f.draws...)
}

func (f *fakeDesk) posted() ([]AnswerRow, []map[string]any) {
	f.service.Lock()
	defer f.service.Unlock()
	return append([]AnswerRow(nil), f.answers...), append([]map[string]any(nil), f.logs...)
}

// started is a watch that has run its start and everything the start began.
func (f *fakeDesk) started() *Watch {
	watch := NewWatch(f.desk)
	watch.Start(context.Background())
	watch.Wait()
	return watch
}

// tick moves the clock by, ticks, and waits for everything the tick began.
func (f *fakeDesk) tick(watch *Watch, by time.Duration) {
	f.advance(by)
	watch.Tick(context.Background())
	watch.Wait()
}

// readyNudge ticks a fresh watch at the interval until its nudge is ready.
func (f *fakeDesk) readyNudge(watch *Watch) Nudge {
	f.t.Helper()
	for range 10 {
		f.tick(watch, tickCap)
		if state := f.state(); state.Phase == Ready {
			return *state.Nudge
		}
	}
	f.t.Fatalf("the nudge never turned ready: %+v", f.state())
	return Nudge{}
}
