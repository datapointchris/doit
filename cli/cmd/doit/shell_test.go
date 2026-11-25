package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A gate that does not parse breaks at shell startup on every desk at once,
// rather than where it was written.
func TestTheStartupGateDoitShellPrintsParsesInZsh(t *testing.T) {
	if _, err := exec.LookPath("zsh"); err != nil {
		t.Skip("zsh is not installed")
	}
	run := runDoitWith(t, nil, "shell", "init", "zsh")
	parse := exec.CommandContext(t.Context(), "zsh", "-n")
	parse.Stdin = strings.NewReader(run.stdout)
	if out, err := parse.CombinedOutput(); run.exit != 0 || err != nil {
		t.Errorf("doit shell init zsh exited %d, and zsh -n refused it: %v\n%s", run.exit, err, out)
	}
}

// A Tab runs `doit __complete`, which has to answer from this binary and the
// names file alone, with no request to the service.
func TestATabCompletesEveryVerbAndThePursuitNamesFromTheNamesFile(t *testing.T) {
	cache := t.TempDir()
	if err := os.MkdirAll(filepath.Join(cache, "doit"), 0o755); err != nil {
		t.Fatal(err)
	}
	names := "chores\tThe maintenance list\nread\tRead a book\n"
	if err := os.WriteFile(filepath.Join(cache, "doit", "next-names.txt"), []byte(names), 0o600); err != nil {
		t.Fatal(err)
	}
	env := []string{"XDG_CACHE_HOME=" + cache}

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
}
