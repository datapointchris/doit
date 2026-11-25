package tui

import (
	"context"
	"io"
	"path/filepath"
	"slices"

	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/deskfiles"
)

// The frame --once draws where COLUMNS and LINES are unset and stdout is no terminal.
const (
	onceFallbackWidth  = 120
	onceFallbackHeight = 40
)

// Inputs is what the tabs' boards are built from.
type Inputs struct {
	// Panel is the desk nudge panel, which the now tab reads and picks through.
	Panel Panel
}

// tab is one tab: its name, its live board, and its print for --once and --json.
type tab struct {
	name  string
	board func(in Inputs) gotermboard.Board
	print func(ctx context.Context, out io.Writer, asJSON bool, in Inputs, pal gotermstyle.Palette) error
}

// tabs is every tab, in the order Tab cycles them.
func tabs() []tab {
	return []tab{{
		name:  NowTab,
		board: func(in Inputs) gotermboard.Board { return NewNowBoard(in.Panel) },
		print: func(ctx context.Context, out io.Writer, asJSON bool, in Inputs, pal gotermstyle.Palette) error {
			return printBoard(ctx, out, asJSON, NewNowBoard(in.Panel), pal)
		},
	}}
}

// TabNames is every tab's name, in the order Tab cycles them.
func TabNames() []string {
	all := tabs()
	names := make([]string, len(all))
	for i, each := range all {
		names[i] = each.name
	}
	return names
}

// DisplayFile is where the live program keeps each tab's display and the tab last moved to.
func DisplayFile() string { return filepath.Join(deskfiles.StateDir(), "tui-display.json") }

// Program is the live program of every tab, keeping its display in DisplayFile. It opens on the
// tab that view names. With no view, it opens where the last run was left.
func Program(in Inputs, pal gotermstyle.Palette, view string) (*gotermboard.Tabs, error) {
	all := tabs()
	live := make([]gotermboard.Tab, len(all))
	for i, each := range all {
		live[i] = gotermboard.Tab{Name: each.name, Board: each.board(in)}
	}
	program, err := gotermboard.NewTabs(live, pal, view)
	if err != nil {
		return nil, err
	}
	program.RemembersDisplayIn(DisplayFile())
	return program, nil
}

// Print reads the tab view names once, the first where it names none, and prints its frame, or
// its snapshot where asJSON is set. It builds no other tab.
func Print(ctx context.Context, out io.Writer, in Inputs, view string, asJSON bool, pal gotermstyle.Palette) error {
	all := tabs()
	at := slices.IndexFunc(all, func(each tab) bool { return each.name == view })
	return all[max(at, 0)].print(ctx, out, asJSON, in, pal)
}

func printBoard[S any](ctx context.Context, out io.Writer, asJSON bool, board gotermboard.SnapshotBoard[S], pal gotermstyle.Palette) error {
	gotermboard.RefreshAll(ctx, board.Sources()...)
	return gotermboard.PrintTab(out, asJSON, board, pal, onceFallbackWidth, onceFallbackHeight)
}
