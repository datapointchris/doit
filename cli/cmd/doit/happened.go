package main

import (
	"errors"
	"net/http"
	"os"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newHappenedCommand() *cobra.Command {
	var since, until string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "happened",
		Short: "What happened over a span of time, from every app and log, and who did it",
		Long: "What happened over a span of time, from every app and log, and who did it.\n\n" +
			"`doit today` folds the same records over the one day, beside what the day still owes.",
		Example: "  doit happened                                         the service's default window, which the title names\n" +
			"  doit happened --since 30d                             the past month\n" +
			"  doit happened --since 2026-11-01 --until 2026-11-03  three whole days, since a day includes all of itself\n" +
			"  doit happened --json                                  every group as data",
		GroupID: whatCameOfIt,
		Args:    cobra.NoArgs,
		// A read that asked some records exits 0 and names the rest in its
		// verdict. One that could read none exits 1, so a script can tell it
		// from a quiet window.
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, answer, err := client.Happened(cmd.Context(), since, until)
			if err != nil {
				return windowRefused(err)
			}
			sourceProblems(cmd.ErrOrStderr(), answer.Problems)
			if asJSON {
				err = printJSON(cmd.OutOrStdout(), raw)
			} else {
				err = screens.Happened(cmd.OutOrStdout(), answer, until == "", screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
			}
			if err != nil {
				return err
			}
			if len(answer.Unavailable) == len(answer.Asked) {
				return exitCode(1)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&since, "since", "",
		"Start of the window: a span back from now such as 7d, a day such as 2026-11-01, or an RFC 3339 instant. "+
			"The service picks it where neither end is given, and the earliest record where only --until is")
	cmd.Flags().StringVar(&until, "until", "", "End of the window, in the same forms; a day includes all of itself. Now where omitted")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output every group as JSON to stdout")
	return cmd
}

// windowRefused is the service refusing the window --since and --until name,
// which is how the command was typed, so it exits 2.
func windowRefused(err error) error {
	var refused *service.Refused
	if errors.As(err, &refused) && refused.Status == http.StatusUnprocessableEntity {
		return goclikit.UsageError(err)
	}
	return err
}
