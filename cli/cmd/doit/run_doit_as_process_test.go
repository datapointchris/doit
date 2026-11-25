package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// runAsDoit makes this test binary run main, so a test runs doit as a process
// with the os.Args, PATH and stdin that it chooses.
const runAsDoit = "DOIT_TEST_RUN_AS_DOIT"

func TestMain(m *testing.M) {
	if os.Getenv(runAsDoit) == "1" {
		main()
		return
	}
	os.Exit(m.Run())
}

type doitRun struct {
	stdout, stderr string
	exit           int
}

// runDoitWith runs doit with env added to a temporary home. Its stdin and
// stdout are no terminal.
func runDoitWith(t *testing.T, env []string, args ...string) doitRun {
	t.Helper()
	cmd := exec.CommandContext(t.Context(), os.Args[0], args...)
	cmd.Env = append([]string{
		runAsDoit + "=1",
		"HOME=" + t.TempDir(),
		"XDG_STATE_HOME=" + t.TempDir(),
		"XDG_CONFIG_HOME=" + t.TempDir(),
	}, env...)
	cmd.Stdin = strings.NewReader("one line\n")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exited *exec.ExitError
	if err != nil && !errors.As(err, &exited) {
		t.Fatalf("doit %v: %v", args, err)
	}
	return doitRun{stdout.String(), stderr.String(), cmd.ProcessState.ExitCode()}
}
