package main

import (
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
	}
}

func newUpdateCommand() *cobra.Command {
	cmd := goclikit.UpdateCommand(updateConfig())
	cmd.GroupID = thisMachine
	return cmd
}
