package main

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/datapointchris/goclikit"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/passthrough"
)

// newTUICommand is `doit tui`, which execs doit-python. It is in this tree so
// the help lists it and a Tab completes it. Its arguments reach doit-python
// unparsed, so `doit tui --help` is doit-python's help, and `doit help tui`
// execs the same. The exec ends this process, so it starts no update check.
func newTUICommand() *cobra.Command {
	cmd := goclikit.StartsNoUpdateCheck(&cobra.Command{
		Use:                "tui",
		Short:              "The screens where a click or a key completes a row",
		GroupID:            whatToDoNow,
		DisableFlagParsing: true,
		ValidArgsFunction:  cobra.NoFileCompletions,
		RunE: func(_ *cobra.Command, args []string) error {
			return execPythonTUI(pythonTUIArgv(args))
		},
	})
	// A help function returns no error, and `help` starts no update check that
	// an exit here could cut off.
	cmd.SetHelpFunc(func(cmd *cobra.Command, _ []string) {
		if err := execPythonTUI([]string{"tui", "--help"}); err != nil {
			_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "doit:", err)
			os.Exit(1)
		}
	})
	return cmd
}

// execPythonTUI replaces this process with doit-python run on argv.
func execPythonTUI(argv []string) error {
	err := passthrough.Exec(argv)
	if errors.Is(err, passthrough.ErrNotInstalled) {
		return fmt.Errorf("%w, and it runs `doit tui`", err)
	}
	return err
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
