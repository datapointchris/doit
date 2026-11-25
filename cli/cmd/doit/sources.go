package main

import (
	"fmt"
	"os"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"

	"github.com/datapointchris/gotermstyle"
)

func newSourcesCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "sources",
		Short: "Which apps doit asks, and what it asks them",
		Long: "Which apps doit asks, and what it asks them.\n\n" +
			"A source is one app doit reads, and it becomes one or more lanes on the dashboard. Which\n" +
			"apps those are is yours to declare, in the document `doit sources edit` opens.",
		Example: "  doit sources list  every configured source, and whether it answers\n" +
			"  doit sources edit  turn one on or off",
		GroupID: whatYouDeclared,
	})
	cmd.AddCommand(newSourcesListCommand(), newSourcesExampleCommand(), newSourcesEditCommand(), newSourcesContractCommand())
	return cmd
}

func newSourcesListCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "Every configured source, and whether the service's read of it answers",
		Example: "  doit sources list         every configured source, and whether it answers\n" +
			"  doit sources list --json  the same answers as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			answers, sources, err := client.Sources(cmd.Context())
			if err != nil {
				return err
			}
			errOut := cmd.ErrOrStderr()
			for _, problem := range sources.Problems {
				_, _ = fmt.Fprintln(errOut, yellow(errOut, "sources: "+terminalcontrol.Strip(problem)))
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), answers)
			}
			return screens.SourcesList(cmd.OutOrStdout(), sources, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newSourcesExampleCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "example",
		Short:   "Print the annotated example of the sources, writing nothing",
		Example: "  doit sources example  see what the document can say, before writing one",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			template, err := client.DocumentTemplate(cmd.Context(), "sources")
			if err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), template.Text)
			return err
		},
	}
}

func newSourcesEditCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "edit",
		Short:   "Edit the sources in your editor, starting from the example where none are written",
		Example: "  doit sources edit  turn a source on or off",
		Args:    cobra.NoArgs,
		RunE:    func(cmd *cobra.Command, _ []string) error { return editedOrExit(cmd, "sources") },
	}
}

func newSourcesContractCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "contract",
		Short: "The lane document every source becomes, as an annotated example",
		Long: "The lane document every source becomes, as an annotated example.\n\n" +
			"This is the interface between a source and every view: each adapter's read is shaped into it.",
		Example: "  doit sources contract  the shape every lane takes, for a source you are writing",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			contract, err := client.SourcesContract(cmd.Context())
			if err != nil {
				return err
			}
			return printJSON(cmd.OutOrStdout(), contract)
		},
	}
}
