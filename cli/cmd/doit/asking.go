package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/charmbracelet/x/term"
)

// errAbandoned is a question left with Ctrl-D, so nothing is written.
var errAbandoned = errors.New("abandoned")

// answers reads what the person types, one line per question.
var answers = bufio.NewReader(os.Stdin)

// canAsk is whether anyone can be asked: --no-input never allows it, and
// otherwise stdin is a terminal. A question written to a stdin that never
// closes leaves its caller with no output and no exit code.
func canAsk() bool { return !noInput && term.IsTerminal(os.Stdin.Fd()) }

// asked prints prompt to out and reads one line, trimmed. End of input abandons the question.
func asked(out io.Writer, prompt string) (string, error) {
	_, _ = fmt.Fprint(out, prompt)
	text, err := answers.ReadString('\n')
	if err != nil && text == "" {
		_, _ = fmt.Fprintln(out)
		return "", errAbandoned
	}
	return strings.TrimSpace(text), nil
}

// confirmed asks a yes-or-no question whose answer is no unless typed.
func confirmed(out io.Writer, prompt string) bool {
	answer, err := asked(out, prompt)
	return err == nil && (strings.EqualFold(answer, "y") || strings.EqualFold(answer, "yes"))
}
