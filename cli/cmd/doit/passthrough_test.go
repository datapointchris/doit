package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

// runAsDoit makes this test binary run main, so a test runs doit as a process
// with the os.Args, PATH and stdin that it chooses.
const runAsDoit = "DOIT_TEST_RUN_AS_DOIT"

const handedTreeFile = "testdata/tree-handed-to-doit-python.json"

func TestMain(m *testing.M) {
	if os.Getenv(runAsDoit) == "1" {
		main()
		return
	}
	os.Exit(m.Run())
}

// fakePython writes a doit-python that records its argv one NUL-ended word at
// a time and the Go tree it was handed, echoes stdin, and exits 7. Only shell
// builtins run, so PATH needs nothing beyond the fake.
func fakePython(t *testing.T) (dir, argvFile string) {
	t.Helper()
	dir = t.TempDir()
	argvFile = filepath.Join(t.TempDir(), "argv")
	script := "#!/bin/sh\n" +
		": >\"$FAKE_ARGV\"\n" +
		"printf '%s' \"$DOIT_GO_COMMANDS\" >\"$FAKE_ARGV.commands\"\n" +
		"for word in \"$@\"; do printf '%s\\0' \"$word\" >>\"$FAKE_ARGV\"; done\n" +
		"while IFS= read -r line; do printf 'python read %s\\n' \"$line\"; done\n" +
		"exit 7\n"
	if err := os.WriteFile(filepath.Join(dir, "doit-python"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return dir, argvFile
}

type doitRun struct {
	stdout, stderr string
	exit           int
}

func runDoit(t *testing.T, path, argvFile string, args ...string) doitRun {
	t.Helper()
	return runDoitWith(t, []string{"PATH=" + path, "FAKE_ARGV=" + argvFile}, args...)
}

// runDoitWith runs doit with env added to a temporary home.
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

func passedArgv(t *testing.T, argvFile string) ([]string, bool) {
	t.Helper()
	data, err := os.ReadFile(argvFile)
	if errors.Is(err, os.ErrNotExist) {
		return nil, false
	}
	if err != nil {
		t.Fatal(err)
	}
	words := strings.Split(string(data), "\x00")
	return words[:len(words)-1], true
}

func TestAnUnportedVerbExecsDoitPythonWithTheSameArgvAndExitCodeAndAMissingOneIsNamed(t *testing.T) {
	fake, argvFile := fakePython(t)

	for _, args := range [][]string{
		{"config", "show", "--json", "a word with spaces"},
		{"auth", "status", "--bogus"},
		{"--no-input", "auth", "bogus"},
		{},
		{"--help"},
	} {
		_ = os.Remove(argvFile)
		run := runDoit(t, fake, argvFile, args...)
		got, reached := passedArgv(t, argvFile)
		if !reached || !slices.Equal(got, args) {
			t.Errorf("doit %q: doit-python got %q (reached %v), want the argv unchanged", args, got, reached)
		}
		if run.exit != 7 || run.stdout != "python read one line\n" {
			t.Errorf("doit %q: exit %d, stdout %q; want doit-python's exit 7 and its read of stdin", args, run.exit, run.stdout)
		}
	}

	// doit-python's own tests list the Go commands from this file, so it holds
	// what the Go doit hands on. A summary is help prose, so only each command's
	// path and group are compared.
	var handed, committed []treeCommand
	if data, err := os.ReadFile(argvFile + ".commands"); err != nil || json.Unmarshal(data, &handed) != nil {
		t.Fatalf("doit-python was handed no readable Go tree: %v %q", err, data)
	}
	if data, err := os.ReadFile(handedTreeFile); err != nil || json.Unmarshal(data, &committed) != nil {
		t.Fatalf("reading %s: %v", handedTreeFile, err)
	}
	for _, tree := range [][]treeCommand{handed, committed} {
		for i := range tree {
			tree[i].Summary = ""
		}
	}
	if !reflect.DeepEqual(handed, committed) {
		t.Errorf("doit-python was handed %+v, and %s holds %+v; write the first there", handed, handedTreeFile, committed)
	}

	for _, args := range [][]string{{"--version"}, {"-V"}, {"--no-input", "--version"}, {"auth", "--help"}} {
		_ = os.Remove(argvFile)
		run := runDoit(t, fake, argvFile, args...)
		if _, reached := passedArgv(t, argvFile); reached || run.exit != 0 {
			t.Errorf("doit %q: reached doit-python %v, exit %d; want the Go doit to answer: %s", args, reached, run.exit, run.stderr)
		}
	}

	// Without doit-python, the Go doit answers its own help and usage
	// mistakes, and names a verb only doit-python runs.
	for _, missing := range []struct {
		args []string
		exit int
		verb string
	}{
		{[]string{"shell", "init"}, 1, "shell"},
		{[]string{"--no-input", "shell", "init"}, 1, "shell"},
		{[]string{"--help"}, 0, ""},
		{[]string{"auth", "bogus"}, 2, ""},
	} {
		run := runDoit(t, t.TempDir(), argvFile, missing.args...)
		if run.exit != missing.exit || !strings.Contains(run.stderr, missing.verb) {
			t.Errorf("doit %q with no doit-python on PATH: exit %d, stderr %q; want exit %d naming %q",
				missing.args, run.exit, run.stderr, missing.exit, missing.verb)
		}
	}
}
