package main

import (
	"errors"
	"os"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/term"
	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/desknudge"
	"github.com/datapointchris/doit/cli/internal/tui"
)

func newTUICommand() *cobra.Command {
	var view string
	var once, asJSON bool
	cmd := &cobra.Command{
		Use:     "tui",
		Short:   "The screens where a click or a key completes a row",
		GroupID: whatToDoNow,
		Long: "The screens where a click or a key completes a row, in one program with a tab per view.\n\n" +
			"The now tab holds the suggestion, the answers while a desk nudge is ready, and every row\n" +
			"of today that something completes, grouped as the desk nudge panel groups them. Click a\n" +
			"row, then click it again or press enter, to complete it. A timed pursuit opens its\n" +
			"minutes under its row: + and - change them, and enter logs them. A click on a footer hint\n" +
			"does what its key does. It reads the rows as `doit desk-nudge panel show` does, from the\n" +
			"files the desk nudge watch writes, and hands each pick to the watch as `doit desk-nudge\n" +
			"panel pick` does.\n\n" +
			"The next run opens on the tab last moved to, unless --view names one. Each tab keeps its\n" +
			"panel, its summaries, and the groups folded and rows opened, in\n" +
			"$XDG_STATE_HOME/doit/tui-display.json.\n\n" +
			"--once prints one frame of the tab at COLUMNS by LINES, and --json the snapshot it is\n" +
			"drawn from, so an agent reads what a person reads. A program that reads the panel's rows\n" +
			"reads `doit desk-nudge panel show --json`. With no terminal and neither flag it refuses,\n" +
			"and exits 2.",
		Example: "  doit tui                                open on the tab last moved to\n" +
			"  doit tui --view now                     open on the now tab\n" +
			"  COLUMNS=120 LINES=30 doit tui --once    print the opening tab's frame, then exit\n" +
			"  doit tui --view now --json              print the now tab's snapshot",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := gotermboard.RefuseUnknownView(view, tui.TabNames()); err != nil {
				return goclikit.UsageError(err)
			}
			if once && asJSON {
				return goclikit.UsageError(errors.New("--once prints a frame and --json a snapshot, so give one of them"))
			}
			printing := once || asJSON
			if !printing && !onTerminal() {
				return goclikit.UsageError(errors.New("doit tui needs a terminal; --once prints one frame and --json the snapshot"))
			}
			in := tui.Inputs{Panel: tui.DeskPanel{Desk: desknudge.ThisDesk(version)}}
			pal := gotermstyle.PaletteFor(os.Stdout)
			if printing {
				return tui.Print(cmd.Context(), cmd.OutOrStdout(), in, view, asJSON, pal)
			}
			program, err := tui.Program(in, pal, view)
			if err != nil {
				return err
			}
			_, err = tea.NewProgram(program).Run()
			return err
		},
	}
	cmd.Flags().StringVar(&view, "view", "", "Open on this tab: "+strings.Join(tui.TabNames(), ", "))
	_ = cmd.RegisterFlagCompletionFunc("view", cobra.FixedCompletions(tui.TabNames(), cobra.ShellCompDirectiveNoFileComp))
	cmd.Flags().BoolVar(&once, "once", false, "Print one frame of the tab at COLUMNS by LINES, then exit")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output the tab's snapshot as JSON to stdout")
	return cmd
}

// onTerminal is whether a person holds the keyboard and sees the screen.
func onTerminal() bool {
	return term.IsTerminal(os.Stdin.Fd()) && term.IsTerminal(os.Stdout.Fd())
}
