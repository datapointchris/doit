package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newTodayCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "today",
		Short: "What today still owes, above what it has had",
		Long: "What today still owes, above what it has had.\n\n" +
			"Only what a day can finish, where `doit dashboard` lists everything outstanding.",
		Example: "  doit today         what the day still owes, above what it has had\n" +
			"  doit today --json  the same day as data, where each lane says whether it was read",
		GroupID: whatToDoNow,
		Args:    cobra.NoArgs,
		// A read-only glance exits 0 once the service answers. Each group says
		// whether it was read, so a reader of --json checks a lane's status
		// rather than the exit code.
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			if asJSON {
				raw, err := client.Raw(cmd.Context(), "/today", nil)
				if err != nil {
					return err
				}
				var said struct {
					Problems []string `json:"problems"`
				}
				_ = json.Unmarshal(raw, &said)
				sourceProblems(cmd.ErrOrStderr(), said.Problems)
				return printJSON(cmd.OutOrStdout(), raw)
			}
			answer, err := client.Today(cmd.Context())
			if err != nil {
				return err
			}
			sourceProblems(cmd.ErrOrStderr(), answer.Problems)
			return screens.Today(cmd.OutOrStdout(), answer, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output the day as JSON to stdout")
	return cmd
}

// sourceProblems prints each problem in the sources document on stderr, so stdout stays the answer.
func sourceProblems(w io.Writer, problems []string) {
	for _, problem := range problems {
		_, _ = fmt.Fprintln(w, "sources:", problem)
	}
}
