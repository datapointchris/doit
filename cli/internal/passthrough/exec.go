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

// CommandsVariable carries the Go tree's commands to Program, which lists each
// one its own tree lacks in the help it prints.
const CommandsVariable = "DOIT_GO_COMMANDS"

// ErrNotInstalled is Program missing from PATH.
var ErrNotInstalled = errors.New(Program + " is not on PATH")

// Exec replaces this process with Program run on args, with commands in
// CommandsVariable, and returns only when that cannot happen.
func Exec(args []string, commands string) error {
	path, err := exec.LookPath(Program)
	if errors.Is(err, exec.ErrNotFound) {
		return ErrNotInstalled
	}
	if err != nil {
		return fmt.Errorf("find %s: %w", Program, err)
	}
	environment := append(os.Environ(), CommandsVariable+"="+commands)
	if err := syscall.Exec(path, append([]string{Program}, args...), environment); err != nil {
		return fmt.Errorf("run %s: %w", path, err)
	}
	return nil
}
