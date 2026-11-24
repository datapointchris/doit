package main

import (
	"context"
	"encoding/json"
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

// The help's sections. doit-python draws a panel of the same title for each, and
// lists a Go command under its group's.
const (
	whatToDoNow     = "now"
	whatCameOfIt    = "came-of-it"
	whatYouDeclared = "declared"
	thisMachine     = "machine"
)

// portedWhole marks a namespace whose every command this binary runs. Its bare
// and --help lines stay here. An unmarked namespace's go to doit-python, whose
// help lists the commands of both trees.
const portedWhole = "doit.ported-whole"

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
	root.AddCommand(newAuthCommand(), newUpdateCommand())
	return root
}

// owns reports whether this binary runs args itself. A command line it does not
// own goes to doit-python whole:
//   - bare doit and doit --help, so the help lists every verb while any is unported
//   - a bare or --help line on a namespace not marked portedWhole, for the same reason
//   - a verb cobra's tree does not hold, or a word under a namespace it holds
//     that names none of its commands
//   - a held verb given a flag Go does not parse, as Mercurial's rhg falls back
//     to Python hg on an extension it does not support
func owns(args []string) bool {
	root := newRootCommand()
	target, rest, err := root.Find(args)
	if err != nil || target == root {
		return asksVersion(root, args)
	}
	target.InitDefaultHelpFlag()
	if target.ParseFlags(rest) != nil {
		return false
	}
	if !target.HasSubCommands() {
		return true
	}
	_, whole := target.Annotations[portedWhole]
	return whole && target.Flags().NArg() == 0
}

// asksVersion is a line of root options alone that asks for the version, which
// this binary answers with its own release.
func asksVersion(root *cobra.Command, args []string) bool {
	if root.ParseFlags(args) != nil {
		return false
	}
	asked, err := root.Flags().GetBool("version")
	return err == nil && asked && root.Flags().NArg() == 0
}

// unportedVerb is the verb of a passed-on line that only doit-python runs. It
// is "" where the line names a command in this tree, or no verb at all. Without
// doit-python, this binary answers those itself, with its help or its usage error.
func unportedVerb(args []string) string {
	root := newRootCommand()
	if target, _, _ := root.Find(args); target != root {
		return ""
	}
	root.InitDefaultHelpFlag()
	if root.ParseFlags(args) != nil {
		return ""
	}
	return root.Flags().Arg(0)
}

// treeCommand is one command of this tree, as doit-python reads
// passthrough.CommandsVariable.
type treeCommand struct {
	Path    []string `json:"path"`
	Summary string   `json:"summary"`
	Group   string   `json:"group,omitempty"`
}

// treeCommands lists each available command of root's tree by its path below
// doit, with its summary and its group's title.
func treeCommands(root *cobra.Command) string {
	commands := []treeCommand{}
	var walk func(*cobra.Command, []string)
	walk = func(parent *cobra.Command, above []string) {
		for _, child := range parent.Commands() {
			if !child.IsAvailableCommand() {
				continue
			}
			path := append(slices.Clone(above), child.Name())
			command := treeCommand{Path: path, Summary: child.Short}
			for _, group := range parent.Groups() {
				if group.ID == child.GroupID {
					command.Group = group.Title
				}
			}
			commands = append(commands, command)
			walk(child, path)
		}
	}
	walk(root, nil)
	encoded, err := json.Marshal(commands)
	if err != nil {
		return "[]"
	}
	return string(encoded)
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
