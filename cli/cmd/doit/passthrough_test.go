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

func TestDoitTuiExecsDoitPythonWithItsArgumentsAndEveryOtherLineStaysInGo(t *testing.T) {
	fake, argvFile := fakePython(t)

	run := runDoit(t, fake, argvFile, "--no-input", "tui", "--tab", "now", "a word with spaces")
	want := []string{"--no-input", "tui", "--tab", "now", "a word with spaces"}
	if got, reached := passedArgv(t, argvFile); !reached || !slices.Equal(got, want) {
		t.Errorf("doit tui: doit-python got %q (reached %v), want %q", got, reached, want)
	}
	if run.exit != 7 || run.stdout != "python read one line\n" {
		t.Errorf("doit tui: exit %d, stdout %q; want doit-python's exit 7 and its read of stdin", run.exit, run.stdout)
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

	for _, line := range []struct {
		args []string
		exit int
	}{
		{nil, 0},
		{[]string{"--help"}, 0},
		{[]string{"--help", "next"}, 0},
		{[]string{"--version"}, 0},
		{[]string{"-V"}, 0},
		{[]string{"--no-input", "--version"}, 0},
		{[]string{"auth", "--help"}, 0},
		{[]string{"config", "show", "--jsn"}, 2},
		{[]string{"bogus"}, 2},
		{[]string{"shell", "init", "fish"}, 2},
	} {
		_ = os.Remove(argvFile)
		run := runDoit(t, fake, argvFile, line.args...)
		if _, reached := passedArgv(t, argvFile); reached || run.exit != line.exit {
			t.Errorf("doit %q: reached doit-python %v, exit %d; want the Go doit to answer with exit %d: %s",
				line.args, reached, run.exit, line.exit, run.stderr)
		}
	}

	run = runDoit(t, t.TempDir(), argvFile, "tui")
	if run.exit != 1 || !strings.Contains(run.stderr, "doit-python") {
		t.Errorf("doit tui with no doit-python on PATH: exit %d, stderr %q; want exit 1 naming doit-python", run.exit, run.stderr)
	}
}

// A Tab runs `doit __complete`, which has to answer from this binary and the
// names file alone. Neither doit-python nor the service may be asked.
func TestATabCompletesEveryVerbAndThePursuitNamesWithNoPythonAndNoRequest(t *testing.T) {
	fake, argvFile := fakePython(t)
	cache := t.TempDir()
	if err := os.MkdirAll(filepath.Join(cache, "doit"), 0o755); err != nil {
		t.Fatal(err)
	}
	names := "chores\tThe maintenance list\nread\tRead a book\n"
	if err := os.WriteFile(filepath.Join(cache, "doit", "next-names.txt"), []byte(names), 0o600); err != nil {
		t.Fatal(err)
	}
	env := []string{"PATH=" + fake, "FAKE_ARGV=" + argvFile, "XDG_CACHE_HOME=" + cache}

	verbs := runDoitWith(t, env, "__complete", "")
	for _, verb := range []string{"next", "tui", "pursuits", "config", "shell", "desk-nudge"} {
		if !strings.Contains(verbs.stdout, "\n"+verb+"\t") && !strings.HasPrefix(verbs.stdout, verb+"\t") {
			t.Errorf("doit __complete '' offers no %s:\n%s", verb, verbs.stdout)
		}
	}
	for _, verb := range []string{"log", "skip"} {
		offered := runDoitWith(t, env, "__complete", "pursuits", verb, "")
		if want := names + ":4\n"; offered.exit != 0 || offered.stdout != want {
			t.Errorf("doit __complete pursuits %s '' printed %q, want the names file then the no-files directive %q", verb, offered.stdout, want)
		}
	}
	if _, reached := passedArgv(t, argvFile); reached {
		t.Error("a Tab started doit-python")
	}
}
