package main

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goselfupdate"
	"github.com/spf13/cobra"
)

// updateConfig is the Go doit's release stream, tagged cli/vX.Y.Z.
//
// TagPrefix keeps it apart from the Python package's vX.Y.Z tags. GitHub's
// latest release is repository-wide, so without the prefix the update would
// find whichever stream released last.
//
// A local build reports "dev", which is no semantic version, so only a
// released binary replaces itself.
func updateConfig() goselfupdate.Config {
	return goselfupdate.Config{
		Owner:     "datapointchris",
		Repo:      "doit",
		Binary:    "doit",
		Version:   version,
		TagPrefix: "cli/",
		// A function, so the daily check's gate costs no gh subprocess on the
		// runs where it declines to check.
		TokenFunc: githubToken,
	}
}

func newUpdateCommand() *cobra.Command {
	return goclikit.UpdateCommand(updateConfig())
}

// githubToken lifts GitHub's 60 unauthenticated reads an hour: the
// environment's token first, then the one gh stores.
func githubToken() string {
	for _, name := range []string{"GITHUB_TOKEN", "GH_TOKEN"} {
		if token := os.Getenv(name); token != "" {
			return token
		}
	}
	// gh answers from its local store, so ten seconds is a hung gh rather than a
	// slow one. A child of gh can hold the output pipe open past the kill, and
	// WaitDelay closes it.
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "gh", "auth", "token")
	cmd.WaitDelay = time.Second
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
