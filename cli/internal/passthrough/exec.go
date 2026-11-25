// Package passthrough replaces this process with the Python doit, which runs
// `doit tui`.
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

// ErrNotInstalled is Program missing from PATH.
var ErrNotInstalled = errors.New(Program + " is not on PATH")

// Exec replaces this process with Program run on args, and returns only when
// that cannot happen.
func Exec(args []string) error {
	path, err := exec.LookPath(Program)
	if errors.Is(err, exec.ErrNotFound) {
		return ErrNotInstalled
	}
	if err != nil {
		return fmt.Errorf("find %s: %w", Program, err)
	}
	if err := syscall.Exec(path, append([]string{Program}, args...), os.Environ()); err != nil {
		return fmt.Errorf("run %s: %w", path, err)
	}
	return nil
}
