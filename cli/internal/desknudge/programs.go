package desknudge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// The bounds on what the desk nudge starts. Each runs outside the state lock,
// because inside it one would hold every other writer as long.
const (
	// programBound is how long claude-sessions, hyprctl, loginctl, the chime and notify-send may each take.
	programBound = 5 * time.Second
	// onLogBound is how long a pursuit's on_log may take. It completes one item
	// in another app, as a habit's completion does in life.
	onLogBound = 30 * time.Second
	// chimeSound is the XDG sound theme's event that a firing plays, named so the desk's own theme chooses the sound.
	chimeSound = "message-new-instant"
)

// habitBound is how long each life habits call may take: the re-read of a
// day's completions, complete, tick and untick. A test shortens it to reach a
// timeout.
var habitBound = 30 * time.Second

// program is one argv the desk nudge starts, with its bound.
type program struct {
	argv  []string
	bound time.Duration
}

// The programs the desk nudge starts. Each argv is built here from doit's own
// values, never read from the panel's cache or a page.
func claudeSessionsProgram() program {
	return program{[]string{"claude-sessions", "--json"}, programBound}
}

func hyprctlInstancesProgram() program {
	return program{[]string{"hyprctl", "instances", "-j"}, programBound}
}

func hyprctlProgram(instance, query string) program {
	return program{[]string{"hyprctl", "-i", instance, query, "-j"}, programBound}
}

func chimeProgram() program {
	return program{[]string{"canberra-gtk-play", "--id", chimeSound, "--description", "desk nudge"}, programBound}
}

func notifyProgram(summary, body string) program {
	return program{[]string{"notify-send", "-a", "doit", summary, body}, programBound}
}

func idleHintProgram() program {
	return program{[]string{"loginctl", "show-session", "auto", "-p", "IdleHint", "--value"}, programBound}
}

// habitProgram completes one habit in life on day, the desk's day when it was
// picked. Its id is all digits, which the pick checks first.
func habitProgram(habitID, day string) program {
	return program{[]string{"life", "habits", "complete", habitID, "--date", day}, habitBound}
}

// habitsCompletedProgram is life's list of the habits completed on day, which
// a pending habit pick reads again at its commit, because life accepts a
// second completion.
func habitsCompletedProgram(day string) program {
	return program{[]string{"life", "habits", "completed", "--since", day, "--until", day, "--json"}, habitBound}
}

// tickProgram records one set of a checklist item on day, and untickProgram
// takes that day's latest one back. The item's id is all digits, which the
// pick checks first.
func tickProgram(itemID, day string) program {
	return program{[]string{"life", "habits", "tick", itemID, "--date", day}, habitBound}
}

func untickProgram(itemID, day string) program {
	return program{[]string{"life", "habits", "untick", itemID, "--date", day}, habitBound}
}

// setProgram is the call that a tick or an untick of itemID runs on day.
func setProgram(action, itemID, day string) program {
	if action == ActionUntick {
		return untickProgram(itemID, day)
	}
	return tickProgram(itemID, day)
}

// programFailed is a program that was missing, timed out or exited non-zero.
// One that timed out may have done its work.
type programFailed struct {
	message  string
	timedOut bool
	// exited is a program that ran and exited non-zero, with exitCode.
	exited   bool
	exitCode int
}

func (p *programFailed) Error() string { return p.message }

// run is a program's stdout, or a programFailed naming what went wrong.
func (p program) run(ctx context.Context) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, p.bound)
	defer cancel()
	command := exec.CommandContext(ctx, p.argv[0], p.argv[1:]...)
	command.WaitDelay = time.Second
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	err := command.Run()
	switch {
	case errors.Is(err, exec.ErrNotFound):
		return "", &programFailed{message: p.argv[0] + " is not installed"}
	case ctx.Err() != nil:
		return "", &programFailed{message: fmt.Sprintf("%s gave no answer within %s", p.argv[0], p.bound), timedOut: true}
	case err != nil:
		var exited *exec.ExitError
		if !errors.As(err, &exited) {
			return "", &programFailed{message: fmt.Sprintf("%s could not start: %v", p.argv[0], err)}
		}
		said := lastLine(stderr.String())
		message := fmt.Sprintf("%s exited %d", strings.Join(p.argv, " "), exited.ExitCode())
		if said != "" {
			message += ": " + said
		}
		return "", &programFailed{message: message, exited: true, exitCode: exited.ExitCode()}
	}
	return stdout.String(), nil
}

func lastLine(text string) string {
	lines := strings.Split(strings.TrimSpace(text), "\n")
	return strings.TrimSpace(lines[len(lines)-1])
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
	out, err := claudeSessionsProgram().run(ctx)
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
	listed, err := hyprctlInstancesProgram().run(ctx)
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
	out, err := hyprctlProgram(chosen, query).run(ctx)
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
	if _, err := chimeProgram().run(ctx); err != nil {
		logger.Warn("could not play the chime, so only the pill shows the nudge: " + err.Error())
	}
}

// notify is a passing notification, for what the person should know now.
func notify(ctx context.Context, summary, body string) {
	if _, err := notifyProgram(summary, body).run(ctx); err != nil {
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
	out, err := idleHintProgram().run(ctx)
	if err != nil {
		logger.Warn("could not read logind's idle hint, so the panel's parts are read as for a desk in use: " + err.Error())
		return false
	}
	return strings.TrimSpace(out) == "yes"
}

// onLogProgram is a pursuit's on_log as an argv. The template is split into
// arguments first, then each placeholder is filled in, so a label holding a
// space or a quote stays one argument.
func onLogProgram(template string, fill map[string]string) (program, error) {
	words, err := splitWords(template)
	if err != nil {
		return program{}, fmt.Errorf("on_log %q cannot be split into arguments: %w", template, err)
	}
	if len(words) == 0 {
		return program{}, fmt.Errorf("on_log %q names no command", template)
	}
	pairs := make([]string, 0, 2*len(fill))
	for name, value := range fill {
		pairs = append(pairs, "{"+name+"}", value)
	}
	replacer := strings.NewReplacer(pairs...)
	argv := make([]string, len(words))
	for i, word := range words {
		argv[i] = replacer.Replace(word)
	}
	return program{argv, onLogBound}, nil
}

// splitWords splits text as a POSIX shell splits words, honoring single
// quotes, double quotes and backslashes, as Python's shlex.split does.
func splitWords(text string) ([]string, error) {
	var words []string
	var word strings.Builder
	inWord := false
	quote := rune(0)
	escaped := false
	for _, char := range text {
		switch {
		case escaped:
			word.WriteRune(char)
			escaped = false
		case quote == '\'':
			if char == '\'' {
				quote = 0
			} else {
				word.WriteRune(char)
			}
		case quote == '"':
			switch char {
			case '"':
				quote = 0
			case '\\':
				escaped = true
			default:
				word.WriteRune(char)
			}
		case char == '\\':
			escaped, inWord = true, true
		case char == '\'' || char == '"':
			quote, inWord = char, true
		case char == ' ' || char == '\t' || char == '\n':
			if inWord {
				words = append(words, word.String())
				word.Reset()
				inWord = false
			}
		default:
			word.WriteRune(char)
			inWord = true
		}
	}
	if quote != 0 || escaped {
		return nil, errors.New("a quote or a backslash is left open")
	}
	if inWord {
		words = append(words, word.String())
	}
	return words, nil
}

// itoa is the text of minutes for an on_log's {minutes}, empty where none were logged.
func itoa(minutes *int) string {
	if minutes == nil {
		return ""
	}
	return strconv.Itoa(*minutes)
}
