package desknudge

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/datapointchris/doit/cli/internal/programs"
)

// The bounds on what the desk nudge starts. Each runs outside the state lock,
// because inside it one would hold every other writer as long.
const (
	// programBound is how long claude-sessions, hyprctl, loginctl, the chime and notify-send may each take.
	programBound = 5 * time.Second
	// chimeSound is the XDG sound theme's event that a firing plays, named so the desk's own theme chooses the sound.
	chimeSound = "message-new-instant"
)

// habitBound is how long each life habits call may take: the re-read of a
// day's completions, complete, tick and untick. A test shortens it to reach a
// timeout.
var habitBound = 30 * time.Second

// The programs the desk nudge starts. Each argv is built here from doit's own
// values, never read from the panel's cache or a page.
func claudeSessionsProgram() programs.Program {
	return programs.Program{Argv: []string{"claude-sessions", "--json"}, Bound: programBound}
}

func hyprctlInstancesProgram() programs.Program {
	return programs.Program{Argv: []string{"hyprctl", "instances", "-j"}, Bound: programBound}
}

func hyprctlProgram(instance, query string) programs.Program {
	return programs.Program{Argv: []string{"hyprctl", "-i", instance, query, "-j"}, Bound: programBound}
}

func chimeProgram() programs.Program {
	return programs.Program{Argv: []string{"canberra-gtk-play", "--id", chimeSound, "--description", "desk nudge"}, Bound: programBound}
}

func notifyProgram(summary, body string) programs.Program {
	return programs.Program{Argv: []string{"notify-send", "-a", "doit", summary, body}, Bound: programBound}
}

func idleHintProgram() programs.Program {
	return programs.Program{Argv: []string{"loginctl", "show-session", "auto", "-p", "IdleHint", "--value"}, Bound: programBound}
}

// habitProgram completes one habit in life on day, the desk's day when it was
// picked. Its id is all digits, which the pick checks first.
func habitProgram(habitID, day string) programs.Program {
	return programs.Program{Argv: []string{"life", "habits", "complete", habitID, "--date", day}, Bound: habitBound}
}

// habitsCompletedProgram is life's list of the habits completed on day, which
// a habit pick reads again as it commits, because life accepts a second
// completion.
func habitsCompletedProgram(day string) programs.Program {
	return programs.Program{Argv: []string{"life", "habits", "completed", "--since", day, "--until", day, "--json"}, Bound: habitBound}
}

// tickProgram records one set of a checklist item on day, and untickProgram
// takes that day's latest one back. The item's id is all digits, which the
// pick checks first.
func tickProgram(itemID, day string) programs.Program {
	return programs.Program{Argv: []string{"life", "habits", "tick", itemID, "--date", day}, Bound: habitBound}
}

func untickProgram(itemID, day string) programs.Program {
	return programs.Program{Argv: []string{"life", "habits", "untick", itemID, "--date", day}, Bound: habitBound}
}

// setProgram is the call that a tick or an untick of itemID runs on day.
func setProgram(action, itemID, day string) programs.Program {
	if action == ActionUntick {
		return untickProgram(itemID, day)
	}
	return tickProgram(itemID, day)
}

// snapshot is what the desk showed at a moment: every Claude session, and the
// focused window. Each holds the read's answer, or why it could not be read.
type snapshot struct {
	sessions      any
	focusedWindow any
}

func (s *snapshot) addTo(r *Record) {
	if s == nil {
		return
	}
	r.set("sessions", s.sessions)
	r.set("focused_window", s.focusedWindow)
}

type failedRead struct {
	Error string `json:"error"`
}

func takeSnapshot(ctx context.Context) *snapshot {
	return &snapshot{sessions: claudeSessions(ctx), focusedWindow: focusedWindow(ctx)}
}

// claudeSessions is every row of claude-sessions --json, whole, or why it could not be read.
func claudeSessions(ctx context.Context) any {
	out, err := claudeSessionsProgram().Run(ctx)
	if err != nil {
		return failedRead{err.Error()}
	}
	var rows []map[string]any
	if err := json.Unmarshal([]byte(out), &rows); err != nil {
		return failedRead{"claude-sessions --json printed no list"}
	}
	return rows
}

// hyprctl is `hyprctl -i <instance> <query> -j`, parsed into answer, on the
// instance that hyprctl instances lists. A unit started at login never has
// HYPRLAND_INSTANCE_SIGNATURE, so the instance is always named.
func hyprctl(ctx context.Context, query string, answer any) error {
	listed, err := hyprctlInstancesProgram().Run(ctx)
	if err != nil {
		return err
	}
	var instances []struct {
		Instance string  `json:"instance"`
		WlSocket string  `json:"wl_socket"`
		Time     float64 `json:"time"`
	}
	if err := json.Unmarshal([]byte(listed), &instances); err != nil {
		return fmt.Errorf("hyprctl instances -j printed no list: %w", err)
	}
	display := os.Getenv("WAYLAND_DISPLAY")
	chosen, onDisplay := "", false
	newest := -1.0
	for _, row := range instances {
		if row.Instance == "" {
			continue
		}
		here := display != "" && row.WlSocket == display
		if (here && !onDisplay) || (here == onDisplay && row.Time > newest) {
			chosen, newest, onDisplay = row.Instance, row.Time, here
		}
	}
	if chosen == "" {
		return errors.New("hyprctl instances -j lists no Hyprland instance")
	}
	out, err := hyprctlProgram(chosen, query).Run(ctx)
	if err != nil {
		return err
	}
	if err := json.Unmarshal([]byte(out), answer); err != nil {
		return fmt.Errorf("hyprctl %s -j printed no JSON: %w", query, err)
	}
	return nil
}

type window struct {
	Class *string `json:"class"`
	Title *string `json:"title"`
}

// focusedWindow is the focused window's class and title, or why they could not
// be read. With nothing focused, hyprctl prints {}.
func focusedWindow(ctx context.Context) any {
	var found struct {
		Class string `json:"class"`
		Title string `json:"title"`
	}
	if err := hyprctl(ctx, "activewindow", &found); err != nil {
		return failedRead{err.Error()}
	}
	return window{Class: nonEmpty(found.Class), Title: nonEmpty(found.Title)}
}

func nonEmpty(text string) *string {
	if text == "" {
		return nil
	}
	return &text
}

// screensOff is whether every display is off, as hypridle leaves them overnight. A failed read says on.
func screensOff(ctx context.Context) bool {
	var monitors []struct {
		DPMSStatus *bool `json:"dpmsStatus"`
	}
	if hyprctl(ctx, "monitors", &monitors) != nil || len(monitors) == 0 {
		return false
	}
	for _, monitor := range monitors {
		if monitor.DPMSStatus == nil || *monitor.DPMSStatus {
			return false
		}
	}
	return true
}

// chime plays chimeSound, unless every display is off. A failure logs at
// warning, and the flashing pill still shows the nudge.
func chime(ctx context.Context) {
	if screensOff(ctx) {
		logger.Info("every display is off, so the nudge fires without its chime")
		return
	}
	if _, err := chimeProgram().Run(ctx); err != nil {
		logger.Warn("could not play the chime, so only the pill shows the nudge: " + err.Error())
	}
}

// Notify shows a passing notice, for a command whose stderr nobody reads, such
// as one a waybar pill or a key starts.
func Notify(ctx context.Context, summary, body string) { notify(ctx, summary, body) }

// notify is a passing notification, for what the person should know now.
func notify(ctx context.Context, summary, body string) {
	if _, err := notifyProgram(summary, body).Run(ctx); err != nil {
		logger.Warn(fmt.Sprintf("could not notify %q: %v", summary, err))
	}
}

// hyprlandRunning is whether a Hyprland instance's socket is under
// $XDG_RUNTIME_DIR/hypr/, read with no program.
func hyprlandRunning() bool {
	runtime := os.Getenv("XDG_RUNTIME_DIR")
	if runtime == "" {
		return false
	}
	found, _ := filepath.Glob(filepath.Join(runtime, "hypr", "*", ".socket.sock"))
	return len(found) > 0
}

// sessionIdle is whether logind marks the display session idle, which an idle
// listener such as hypridle's sets. A failed read reads as active. It gates a
// timed read of the panel's parts and never moves the clock.
func sessionIdle(ctx context.Context) bool {
	out, err := idleHintProgram().Run(ctx)
	if err != nil {
		logger.Warn("could not read logind's idle hint, so the panel's parts are read as for a desk in use: " + err.Error())
		return false
	}
	return strings.TrimSpace(out) == "yes"
}
