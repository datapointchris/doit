package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"slices"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goselfupdate/autoupdate"
	"github.com/datapointchris/gotermstyle/cobrahelp"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/service"
)

// version is set at build time with -ldflags.
var version = "dev"

// noInput is bound to --no-input, which the Python doit's root declares too.
var noInput bool

func newRootCommand() *cobra.Command {
	root := goclikit.AsNamespace(&cobra.Command{
		Use:   "doit",
		Short: "What to attend to now",
		Long: "What to attend to now, from your pursuits, your review items and what your apps say\n" +
			"is outstanding. This binary runs the verbs below, and doit-python runs the rest.",
		Version:       version,
		SilenceUsage:  true,
		SilenceErrors: true,
		Args:          cobra.ArbitraryArgs,
	})
	root.SetFlagErrorFunc(func(_ *cobra.Command, err error) error {
		return goclikit.UsageError(err)
	})
	cobrahelp.Install(root)
	root.CompletionOptions.DisableDefaultCmd = true
	root.PersistentFlags().BoolVar(&noInput, "no-input", false,
		"Never prompt; fail naming the flag that would have answered")
	// -V as the Python doit spells it, which leaves -v free for a --verbose.
	// Declared before cobra's own, which then stands aside.
	root.Flags().BoolP("version", "V", false, "Show the installed version and exit")
	root.AddCommand(newAuthCommand(), newUpdateCommand())
	return root
}

// owns reports whether this binary runs args itself. A command line it does not
// own goes to doit-python whole:
//   - bare doit and doit --help, so the help lists every verb while any is unported
//   - a verb cobra's tree does not hold, or a word under a namespace it holds
//     that names none of its commands
//   - a held verb given a flag Go does not parse, as Mercurial's rhg falls back
//     to Python hg on an extension it does not support
func owns(args []string) bool {
	if len(args) == 0 {
		return false
	}
	root := newRootCommand()
	target, rest, err := root.Find(args)
	if err != nil || target == root {
		return slices.Equal(args, []string{"--version"}) || slices.Equal(args, []string{"-V"})
	}
	target.InitDefaultHelpFlag()
	if target.ParseFlags(rest) != nil {
		return false
	}
	return !target.HasSubCommands() || target.Flags().NArg() == 0
}

// exitCode lets a command choose its exit status without an error line, such
// as `auth status` reporting "not logged in" as a state rather than a failure.
type exitCode int

func (e exitCode) Error() string { return "" }

// execute runs the tree on os.Args and returns the process's exit status.
func execute() int {
	err := goclikit.Execute(context.Background(), newRootCommand(), autoupdate.Config{Update: updateConfig()})
	var chosen exitCode
	if err != nil && !errors.As(err, &chosen) && !errors.Is(err, goclikit.ErrReported) {
		fmt.Fprintln(os.Stderr, "doit:", err)
	}
	return exitCodeFor(err)
}

// exitCodeFor is 0 for success, 2 for a usage mistake, 4 where no answer came,
// a command's own choice where it made one, and 1 for anything else refused.
func exitCodeFor(err error) int {
	if err == nil {
		return 0
	}
	var chosen exitCode
	if errors.As(err, &chosen) {
		return int(chosen)
	}
	if errors.Is(err, goclikit.ErrUsage) {
		return 2
	}
	return service.ExitCode(err)
}
