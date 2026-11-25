package main

import (
	"context"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// recorder appends its name and each argument, tab-separated, as one line of $FAKE_ARGV.
const recorder = "#!/bin/sh\n" +
	"line=${0##*/}\n" +
	"for word in \"$@\"; do line=\"$line\t$word\"; done\n" +
	"printf '%s\\n' \"$line\" >>\"$FAKE_ARGV\"\n"

func recorded(t *testing.T, argvFile string) [][]string {
	t.Helper()
	data, err := os.ReadFile(argvFile)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var runs [][]string
	for line := range strings.Lines(string(data)) {
		runs = append(runs, strings.Split(strings.TrimSuffix(line, "\n"), "\t"))
	}
	return runs
}

func TestPanelOpenNamesAMissingWatchWithExit4AndANoticeAndAMissingWebviewrsWithExit1(t *testing.T) {
	withWebviewrs, without := t.TempDir(), t.TempDir()
	for _, file := range []string{
		filepath.Join(withWebviewrs, "webviewrs"), filepath.Join(withWebviewrs, "notify-send"), filepath.Join(without, "notify-send"),
	} {
		if err := os.WriteFile(file, []byte(recorder), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// A unix socket's path holds at most 108 bytes, so the runtime directory is short.
	runtime, err := os.MkdirTemp("", "panelopen")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(runtime) })
	argvFile := filepath.Join(t.TempDir(), "argv")
	open := func(path string) doitRun {
		_ = os.Remove(argvFile)
		return runDoitWith(t, []string{"PATH=" + path, "FAKE_ARGV=" + argvFile, "XDG_RUNTIME_DIR=" + runtime}, "desk-nudge", "panel", "open")
	}

	run := open(withWebviewrs)
	runs := recorded(t, argvFile)
	if run.exit != 4 || !strings.Contains(run.stderr, "desk-nudge user service") || len(runs) != 1 ||
		!slices.Equal(runs[0][:4], []string{"notify-send", "-a", "doit", "doit: the panel did not open"}) || !strings.Contains(runs[0][4], "desk-nudge user service") {
		t.Errorf("with no watch: exit %d, stderr %q, ran %q; want exit 4 and a notice, each naming the desk-nudge user service", run.exit, run.stderr, runs)
	}

	run = open(without)
	if run.exit != 1 || !strings.Contains(run.stderr, "webviewrs") || len(recorded(t, argvFile)) != 0 {
		t.Errorf("with no webviewrs: exit %d, stderr %q, ran %q; want exit 1 naming webviewrs, and nothing run", run.exit, run.stderr, recorded(t, argvFile))
	}

	minted := "http://127.0.0.1:41234/?code=fresh"
	if err := os.MkdirAll(filepath.Join(runtime, "doit"), 0o700); err != nil {
		t.Fatal(err)
	}
	listener, err := (&net.ListenConfig{}).Listen(t.Context(), "unix", filepath.Join(runtime, "doit", "watch.sock"))
	if err != nil {
		t.Fatal(err)
	}
	watch := &http.Server{Handler: http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/page-codes" {
			http.NotFound(response, request)
			return
		}
		response.WriteHeader(http.StatusCreated)
		_, _ = response.Write([]byte(`{"url": "` + minted + `", "expires_at": "2026-11-24T18:20:10.000-05:00"}`))
	})}
	go func() { _ = watch.Serve(listener) }()
	t.Cleanup(func() { _ = watch.Shutdown(context.Background()) })

	run = open(withWebviewrs)
	want := []string{"webviewrs", "-n", "doit-panel", "-t", "doit panel", "--dark", "--close-on-blur", minted}
	if runs := recorded(t, argvFile); run.exit != 0 || len(runs) != 1 || !slices.Equal(runs[0], want) {
		t.Errorf("with a watch: exit %d, stderr %q, ran %q; want webviewrs alone, as %q", run.exit, run.stderr, runs, want)
	}
}
