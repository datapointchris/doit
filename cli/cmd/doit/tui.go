package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/passthrough"
)

// newTUICommand is `doit tui`, the one verb doit-python still runs. It is in
// this tree so the help lists it and a Tab completes it. Its arguments reach
// doit-python as they were typed.
func newTUICommand() *cobra.Command {
	return &cobra.Command{
		Use:   "tui",
		Short: "The screens where a click or a key completes a row",
		Long: "Open doit's screens, where a click or a key completes a row.\n\n" +
			"`--tab <name>` opens on that tab, and `--once` prints the screen it opens on at\n" +
			"COLUMNS by LINES, then exits. doit-python runs it, and hands the terminal to doit-tui.",
		Example: "  doit tui                              open doit's screens\n" +
			"  doit tui --tab now                    open on what can be completed now\n" +
			"  COLUMNS=120 LINES=30 doit tui --once  print the screen it opens on, then exit",
		GroupID:            whatToDoNow,
		DisableFlagParsing: true,
		ValidArgsFunction:  cobra.NoFileCompletions,
		Annotations:        map[string]string{startsNoUpdateCheck: ""},
		RunE: func(cmd *cobra.Command, args []string) error {
			err := passthrough.Exec(pythonTUIArgv(args), treeCommands(cmd.Root()))
			if errors.Is(err, passthrough.ErrNotInstalled) {
				return fmt.Errorf("%w, and it runs `doit tui`", err)
			}
			return err
		},
	}
}

// pythonTUIArgv is doit-python's command line for `doit tui` given args. cobra
// hands a command that parses no flags the root's --no-input with the rest,
// and doit-python takes that option only before the verb.
func pythonTUIArgv(args []string) []string {
	var options, rest []string
	for _, word := range args {
		if word == "--no-input" || strings.HasPrefix(word, "--no-input=") {
			options = append(options, word)
		} else {
			rest = append(rest, word)
		}
	}
	return append(append(options, "tui"), rest...)
}
