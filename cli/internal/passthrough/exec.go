// Package passthrough hands a command line the Go doit has not ported to the
// Python doit, which still runs it.
//
// The process is replaced rather than spawned. The Python program then owns
// stdin, stdout, stderr, the terminal's signals and the exit code, as if it had
// been run directly.
package passthrough

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

// Program is the Python doit's console script, which Go finds on PATH.
const Program = "doit-python"

// NotInstalled is the refusal where Program is not on PATH, naming what was asked for.
type NotInstalled struct {
	Verb string
}

func (n NotInstalled) Error() string {
	return fmt.Sprintf("%s is not installed, and %s is not yet in the Go doit", Program, n.Verb)
}

// Exec replaces this process with Program run on args, and returns only when
// that cannot happen.
func Exec(args []string) error {
	path, err := exec.LookPath(Program)
	if errors.Is(err, exec.ErrNotFound) {
		return NotInstalled{Verb: verbOf(args)}
	}
	if err != nil {
		return fmt.Errorf("find %s: %w", Program, err)
	}
	if err := syscall.Exec(path, append([]string{Program}, args...), os.Environ()); err != nil {
		return fmt.Errorf("run %s: %w", path, err)
	}
	return nil
}

// verbOf is what the refusal names: the first word typed after doit.
func verbOf(args []string) string {
	if len(args) == 0 {
		return "bare doit"
	}
	return args[0]
}
