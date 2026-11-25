package main

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goselfupdate/autoupdate"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle/cobrahelp"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/service"
)

// version is set at build time with -ldflags.
var version = "dev"

// noInput is bound to --no-input, which the Python doit's root declares too.
var noInput bool

// The help's sections, in the order the root help lists them.
const (
	whatToDoNow     = "now"
	whatCameOfIt    = "came-of-it"
	whatYouDeclared = "declared"
	thisMachine     = "machine"
)

func newRootCommand() *cobra.Command {
	root := goclikit.AsNamespace(&cobra.Command{
		Use:   "doit",
		Short: "What to attend to now",
		Long: "What to attend to now, from your pursuits, your review items and what your apps say is\n" +
			"outstanding.\n\n" +
			"Most commands are a noun, then a verb: `doit pursuits log`. So a list, log, drift loop on\n" +
			"your pursuits changes only the last word. The views you read most are one word: `doit today`.\n\n" +
			"You never name a file or a machine. doit's service holds your records and computes every\n" +
			"view. This machine's login says where the service is, and `doit auth login <url>` writes it\n" +
			"once.\n\n" +
			"Start with `doit dashboard` to see everything outstanding, or `doit next` to be handed one\n" +
			"thing. The dashboard's last line names any pursuit that is behind, and `doit next` offers\n" +
			"it first.\n\n" +
			"A pursuit is something you mean to keep doing, at a pace you declare, with a weight that\n" +
			"orders it against the others. The draw is what `doit next` offers from them: everything\n" +
			"owed, then the rest picked by weight. A lane is one list on the dashboard: what one app\n" +
			"says is outstanding. A review item is something to revisit on a cadence.",
		Example: "  doit dashboard                       everything outstanding, every lane at a glance\n" +
			"  doit next                            what to do now, drawn from your pursuits\n" +
			"  doit today                           what today still owes, above what it has had\n" +
			"  doit happened --since 7d             what your apps and logs recorded this past week\n" +
			"  doit review due                      every review item due, where the dashboard shows the first few\n" +
			"  doit pursuits log read --minutes 40  record a 40-minute sitting of a timed pursuit",
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
	root.AddGroup(
		&cobra.Group{ID: whatToDoNow, Title: "What to do now"},
		&cobra.Group{ID: whatCameOfIt, Title: "What came of it"},
		&cobra.Group{ID: whatYouDeclared, Title: "What you declared"},
		&cobra.Group{ID: thisMachine, Title: "This machine"},
	)
	root.PersistentFlags().BoolVar(&noInput, "no-input", false,
		"Never prompt; fail naming the flag that would have answered")
	// -V as the Python doit spells it, which leaves -v free for a --verbose.
	// Declared before cobra's own, which then stands aside.
	root.Flags().BoolP("version", "V", false, "Show the installed version and exit")
	root.AddCommand(
		newNextCommand(), newTodayCommand(), newDashboardCommand(), newIngestionCommand(), newTUICommand(),
		newHappenedCommand(), newForecastCommand(),
		newPursuitsCommand(), newReviewCommand(), newSourcesCommand(),
		newAuthCommand(), newConfigCommand(), newShellCommand(), newUpdateCommand(), newDeskNudgeCommand(),
	)
	return root
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
		// A refusal carries the service's words, which may quote stored text.
		fmt.Fprintln(os.Stderr, "doit:", terminalcontrol.Strip(err.Error()))
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
