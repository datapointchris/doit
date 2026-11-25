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

// The frame --once draws where COLUMNS and LINES are unset and stdout is no terminal.
const (
	onceFallbackWidth  = 120
	onceFallbackHeight = 40
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
			"--once prints one frame of the tab at COLUMNS by LINES, and --json the panel document it\n" +
			"is drawn from, so an agent reads what a person reads. With no terminal and neither flag\n" +
			"it refuses, and exits 2.",
		Example: "  doit tui                                   open on the now tab\n" +
			"  doit tui --view now                        open on the now tab by name\n" +
			"  COLUMNS=120 LINES=30 doit tui --once       print the now tab's frame, then exit\n" +
			"  doit tui --view now --json | jq '.rows[]'  the rows the now tab draws, as data",
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
			now := tui.NewNowBoard(tui.DeskPanel{Desk: desknudge.ThisDesk(version)})
			pal := gotermstyle.PaletteFor(os.Stdout)
			if printing {
				gotermboard.RefreshAll(cmd.Context(), now.Sources()...)
				return gotermboard.PrintTab(cmd.OutOrStdout(), asJSON, now, pal, onceFallbackWidth, onceFallbackHeight)
			}
			tabs, err := gotermboard.NewTabs([]gotermboard.Tab{{Name: tui.NowTab, Board: now}}, pal, view)
			if err != nil {
				return err
			}
			_, err = tea.NewProgram(tabs).Run()
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
