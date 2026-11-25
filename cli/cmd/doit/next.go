package main

import (
	"fmt"
	"os"
	"time"

	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/deskfiles"
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
			descriptions := make(map[string]any, len(answer.Pursuits))
			for name, entry := range answer.Pursuits {
				descriptions[name] = entry.Description
			}
			if err := deskfiles.WriteCompletionNames(descriptions); err != nil {
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
