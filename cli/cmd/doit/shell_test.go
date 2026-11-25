package main

import (
	"os/exec"
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
