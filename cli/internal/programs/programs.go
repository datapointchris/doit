// Package programs runs each program doit starts by its argv, within a bound.
// Every argv is built in doit's code from doit's own values, so a field read
// from elsewhere fills one argument and never adds one.
package programs

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"time"
)

// Program is one argv that doit starts, with how long it may take.
type Program struct {
	Argv  []string
	Bound time.Duration
}

// Failed is a program that was missing, timed out or exited non-zero. One that
// timed out may have done its work.
type Failed struct {
	Message  string
	TimedOut bool
	// Exited is a program that ran and exited non-zero, with ExitCode.
	Exited   bool
	ExitCode int
}

func (f *Failed) Error() string { return f.Message }

// Run is the program's stdout, or a Failed naming what went wrong.
func (p Program) Run(ctx context.Context) (string, error) {
	return p.run(ctx, nil)
}

// RunShowing runs the program with its output on out, as a card printed
// inline, rather than kept.
func (p Program) RunShowing(ctx context.Context, out io.Writer) error {
	_, err := p.run(ctx, out)
	return err
}

func (p Program) run(ctx context.Context, shown io.Writer) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, p.Bound)
	defer cancel()
	command := exec.CommandContext(ctx, p.Argv[0], p.Argv[1:]...)
	command.WaitDelay = time.Second
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	if shown != nil {
		command.Stdout, command.Stderr = shown, shown
	}
	err := command.Run()
	switch {
	case errors.Is(err, exec.ErrNotFound):
		return "", &Failed{Message: p.Argv[0] + " is not installed"}
	case ctx.Err() != nil:
		return "", &Failed{Message: fmt.Sprintf("%s gave no answer within %s", p.Argv[0], p.Bound), TimedOut: true}
	case err != nil:
		var exited *exec.ExitError
		if !errors.As(err, &exited) {
			return "", &Failed{Message: fmt.Sprintf("%s could not start: %v", p.Argv[0], err)}
		}
		said := lastLine(stderr.String())
		message := fmt.Sprintf("%s exited %d", strings.Join(p.Argv, " "), exited.ExitCode())
		if said != "" {
			message += ": " + said
		}
		return "", &Failed{Message: message, Exited: true, ExitCode: exited.ExitCode()}
	}
	return stdout.String(), nil
}

func lastLine(text string) string {
	lines := strings.Split(strings.TrimSpace(text), "\n")
	return strings.TrimSpace(lines[len(lines)-1])
}

// ShellJoin quotes each argument that a shell would split, so the command reads as it ran.
func ShellJoin(argv []string) string {
	quoted := make([]string, len(argv))
	for i, word := range argv {
		if word != "" && !strings.ContainsAny(word, " \t\n'\"\\$`!*?[]{}()<>|&;#~") {
			quoted[i] = word
			continue
		}
		quoted[i] = "'" + strings.ReplaceAll(word, "'", `'"'"'`) + "'"
	}
	return strings.Join(quoted, " ")
}

// SplitWords splits text as a POSIX shell splits words, honoring single
// quotes, double quotes and backslashes, as Python's shlex.split does.
func SplitWords(text string) ([]string, error) {
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
