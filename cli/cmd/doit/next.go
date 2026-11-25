package main

import (
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newNextCommand() *cobra.Command {
	var explain, reroll, asJSON bool
	cmd := &cobra.Command{
		Use:   "next",
		Short: "What to do now, drawn from your pursuits",
		Long: "What to do now, drawn from your pursuits.\n\n" +
			"A pursuit is something you mean to keep doing, at a pace you declare. The draw\n" +
			"is this table: everything owed, heaviest first, then the rest picked by weight.\n" +
			"It stands for a while, so running this again shows the same one. `doit dashboard`\n" +
			"lists everything outstanding rather than picking.",
		Example: "  doit next            what to do now, with every other pursuit under it\n" +
			"  doit next --explain  the same draw with every number behind it\n" +
			"  doit next --reroll   a fresh draw, before the standing one expires\n" +
			"  doit next --json     the draw and the state behind it, as data",
		GroupID: whatToDoNow,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			// The service reads a desk's name from its hostname's first label.
			machine, _ := os.Hostname()
			if asJSON {
				raw, err := client.Raw(cmd.Context(), "/next", service.NextQuery(machine, reroll))
				if err != nil {
					return err
				}
				return printJSON(cmd.OutOrStdout(), raw)
			}
			answer, err := client.Next(cmd.Context(), machine, reroll)
			if err != nil {
				return err
			}
			if err := writeNamesCache(answer.Pursuits); err != nil {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "doit: the names the shell completes were not saved:", err)
			}
			widths, palette := screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout)
			if explain {
				return screens.Explain(cmd.OutOrStdout(), answer, widths, palette)
			}
			return screens.Next(cmd.OutOrStdout(), answer, time.Now(), widths, palette)
		},
	}
	cmd.Flags().BoolVar(&explain, "explain", false, "The same draw with every number behind it")
	cmd.Flags().BoolVar(&reroll, "reroll", false, "Force a fresh draw before the standing one expires")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

// namesCachePath is next-names.txt, the name<TAB>description file that the zsh
// completion reads with no process started.
func namesCachePath() string {
	cache := os.Getenv("XDG_CACHE_HOME")
	if cache == "" {
		home, _ := os.UserHomeDir()
		cache = filepath.Join(home, ".cache")
	}
	return filepath.Join(cache, "doit", "next-names.txt")
}

// writeNamesCache rewrites next-names.txt from the register, a pursuit a line, sorted by name.
func writeNamesCache(register map[string]service.RegisterEntry) error {
	path := namesCachePath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	var b strings.Builder
	for _, name := range slices.Sorted(maps.Keys(register)) {
		description := ""
		if said := register[name].Description; said != nil {
			description = fmt.Sprint(said)
		}
		b.WriteString(name + "\t" + description + "\n")
	}
	return os.WriteFile(path, []byte(b.String()), 0o644)
}
