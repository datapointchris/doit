package main

import (
	"os/exec"
	"strings"
	"testing"
)

// A script that does not parse breaks at shell startup on every desk at once,
// rather than where it was written.
func TestEachScriptDoitShellPrintsParsesInZsh(t *testing.T) {
	if _, err := exec.LookPath("zsh"); err != nil {
		t.Skip("zsh is not installed")
	}
	for _, kind := range []string{"init", "completion"} {
		run := runDoitWith(t, nil, "shell", kind, "zsh")
		parse := exec.CommandContext(t.Context(), "zsh", "-n")
		parse.Stdin = strings.NewReader(run.stdout)
		if out, err := parse.CombinedOutput(); run.exit != 0 || err != nil {
			t.Errorf("doit shell %s zsh exited %d, and zsh -n refused it: %v\n%s", kind, run.exit, err, out)
		}
	}
}
