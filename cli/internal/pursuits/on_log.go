package pursuits

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/doit/cli/internal/programs"
)

// OnLogBound is how long a pursuit's on_log may take. It completes one item in
// another app, as a habit's completion does in life.
const OnLogBound = 30 * time.Second

// OnLog is a pursuit's on_log filled in for one choice, ready to run.
type OnLog struct {
	Program programs.Program
	// Command is the argv as a shell would read it, which the log records.
	Command string
}

// OnLogFor is template filled in for chosen, or nil where nothing runs: no
// template, no choice, or an {id} that the choice does not carry.
func OnLogFor(template *string, chosen map[string]any, note string, minutes *int) (*OnLog, error) {
	if template == nil || *template == "" || len(chosen) == 0 {
		return nil, nil
	}
	text := func(field string) string {
		if value, ok := chosen[field]; ok && value != nil {
			return fmt.Sprint(value)
		}
		return ""
	}
	if strings.Contains(*template, "{id}") && text("id") == "" {
		return nil, nil
	}
	minutesText := ""
	if minutes != nil {
		minutesText = strconv.Itoa(*minutes)
	}
	program, err := OnLogProgram(*template, map[string]string{
		"id": text("id"), "label": text("label"), "note": note, "minutes": minutesText, "pursuit": text("pursuit"),
	})
	if err != nil {
		return nil, err
	}
	return &OnLog{Program: program, Command: programs.ShellJoin(program.Argv)}, nil
}

// OnLogProgram is a pursuit's on_log as an argv. The template is split into
// arguments first, then each placeholder is filled in, so a label holding a
// space or a quote stays one argument.
func OnLogProgram(template string, fill map[string]string) (programs.Program, error) {
	words, err := programs.SplitWords(template)
	if err != nil {
		return programs.Program{}, fmt.Errorf("on_log %q cannot be split into arguments: %w", template, err)
	}
	if len(words) == 0 {
		return programs.Program{}, fmt.Errorf("on_log %q names no command", template)
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
	return programs.Program{Argv: argv, Bound: OnLogBound}, nil
}

// Ran runs on_log and answers what it did, as the log's downstream records it.
// A program that could not start ran nothing, which is an error, so nothing is
// logged.
func (o *OnLog) Ran(ctx context.Context) (map[string]any, error) {
	_, err := o.Program.Run(ctx)
	var failed *programs.Failed
	switch {
	case err == nil:
		return map[string]any{"command": o.Command, "ran": true, "exit_code": 0}, nil
	case errors.As(err, &failed) && failed.TimedOut:
		return map[string]any{"command": o.Command, "ran": true, "exit_code": nil, "error": err.Error()}, nil
	case errors.As(err, &failed) && failed.Exited:
		return map[string]any{"command": o.Command, "ran": true, "exit_code": failed.ExitCode, "error": err.Error()}, nil
	}
	return nil, err
}

// Declined is what the log records where the person said no to on_log.
func (o *OnLog) Declined() map[string]any {
	return map[string]any{"command": o.Command, "ran": false, "exit_code": nil}
}

// Failure is how an on_log that ran ended badly, or "" where it exited 0.
func Failure(downstream map[string]any) string {
	if !Acted(downstream) {
		return ""
	}
	code, exited := WholeNumber(downstream["exit_code"])
	switch {
	case exited && *code == 0:
		return ""
	case exited:
		return fmt.Sprintf("exited %d", *code)
	}
	return fmt.Sprint(downstream["error"])
}
