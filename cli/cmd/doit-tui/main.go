// doit-tui is doit's screens in a terminal, which `doit tui` opens.
package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/charmbracelet/x/ansi"
	"github.com/charmbracelet/x/term"
	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"
)

// The frame --once draws where COLUMNS and LINES are unset and stdout is no terminal.
const (
	onceFallbackWidth  = 120
	onceFallbackHeight = 40
)

const nowTab = "now"

func main() {
	if err := newRootCommand().Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "doit-tui:", err)
		os.Exit(1)
	}
}

func newRootCommand() *cobra.Command {
	var tab string
	var once bool
	cmd := &cobra.Command{
		Use:   "doit-tui",
		Short: "doit's screens, where a click or a key completes a row",
		Long: `doit's screens, where a click or a key completes a row.

The now tab holds the suggestion, the answers while a desk nudge is ready, and every
row of today that something completes, grouped as doit groups them. Click a row, then
click it again or press enter, to complete it. A timed pursuit opens its minutes under
its row: + and - change them, and enter logs them. A click on a footer hint does what
its key does.

It reads and picks through doit desk-nudge panel, so doit holds every rule about
what a row is and what a pick does.`,
		Example: `  doit-tui                               open on the now tab
  doit-tui --tab now                     open on the now tab by name
  COLUMNS=120 LINES=30 doit-tui --once   print the screen it opens on, then exit`,
		Args:          cobra.NoArgs,
		SilenceErrors: true,
		SilenceUsage:  true,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return run(cmd.OutOrStdout(), tab, once)
		},
	}
	cmd.Flags().StringVar(&tab, "tab", "", "the tab to open on: "+nowTab)
	cmd.Flags().BoolVar(&once, "once", false, "print the screen it opens on at COLUMNS by LINES, then exit")
	return cmd
}

func run(out io.Writer, tab string, once bool) error {
	tabs := []gotermboard.Tab{{Name: nowTab, Board: newNowBoard(doitPanel{})}}
	opening, err := tabNamed(tabs, tab)
	if err != nil {
		return err
	}
	pal := gotermstyle.PaletteFor(os.Stdout)
	if once {
		board := opening.Board
		gotermboard.RefreshAll(context.Background(), board.Sources()...)
		width, height := gotermboard.OnceSize(onceFallbackWidth, onceFallbackHeight)
		frame := gotermboard.Once(board, pal, width, height)
		if file, ok := out.(*os.File); !ok || !term.IsTerminal(file.Fd()) {
			frame = ansi.Strip(frame)
		}
		_, err := fmt.Fprintln(out, frame)
		return err
	}
	model, err := gotermboard.NewTabs(tabs, pal, tab)
	if err != nil {
		return err
	}
	_, err = tea.NewProgram(model).Run()
	return err
}

// tabNamed is the tab that --tab names, or the first where it names none.
func tabNamed(tabs []gotermboard.Tab, name string) (gotermboard.Tab, error) {
	if name == "" {
		return tabs[0], nil
	}
	names := make([]string, 0, len(tabs))
	for _, tab := range tabs {
		if tab.Name == name {
			return tab, nil
		}
		names = append(names, tab.Name)
	}
	return gotermboard.Tab{}, fmt.Errorf("no tab %q: the tabs are %s", name, strings.Join(names, ", "))
}
