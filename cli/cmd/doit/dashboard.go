package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"slices"
	"strings"

	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newDashboardCommand() *cobra.Command {
	var lanes []string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "dashboard",
		Short: "Every lane: what is outstanding across your apps",
		Long: "Every lane: what is outstanding across your apps.\n\n" +
			"A lane is one list on this screen, what one app says is outstanding. Nothing\n" +
			"ranks across lanes, so `doit next` is the command that picks one thing. A lane\n" +
			"that could not be read says why, and the last line names any pursuit that is behind.",
		Example: "  doit dashboard               every lane at a glance\n" +
			"  doit dashboard --lane tasks  one lane, with all its rows\n" +
			"  doit dashboard --json        the lanes as data, where each says whether it was read",
		GroupID: whatToDoNow,
		Args:    cobra.NoArgs,
		// A read-only glance exits 0 once the service answers. Each lane says
		// whether it was read, so a reader of --json checks a lane's status
		// rather than the exit code.
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			if asJSON {
				raw, err := client.Raw(cmd.Context(), "/dashboard", service.DashboardQuery(lanes))
				if err != nil {
					return err
				}
				var said service.Dashboard
				_ = json.Unmarshal(raw, &said)
				dashboardNotices(cmd.ErrOrStderr(), said, lanes)
				return printJSON(cmd.OutOrStdout(), raw)
			}
			answer, err := client.Dashboard(cmd.Context(), lanes)
			if err != nil {
				return err
			}
			dashboardNotices(cmd.ErrOrStderr(), answer, lanes)
			return screens.Dashboard(cmd.OutOrStdout(), answer, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().StringArrayVar(&lanes, "lane", nil,
		"Show only this lane, with more of its rows (repeatable). The dashboard names every lane the sources answer")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output the lanes as JSON to stdout")
	return cmd
}

// dashboardNotices say on stderr what the screen cannot: each problem in the
// sources document, that none is configured, and each --lane that named no
// lane, beside the lanes there are. A lane name is checked against what came
// back rather than a closed set, because a conforming source offers lanes
// doit has never heard of.
func dashboardNotices(w io.Writer, answer service.Dashboard, asked []string) {
	sourceProblems(w, answer.Problems)
	if answer.Configured == 0 {
		_, _ = fmt.Fprintln(w, "No sources configured. Write them with doit sources edit.")
	}
	var unmatched []string
	for _, name := range asked {
		if !slices.Contains(answer.LaneNames, name) {
			unmatched = append(unmatched, name)
		}
	}
	if len(unmatched) == 0 {
		return
	}
	offered := "nothing"
	if len(answer.LaneNames) > 0 {
		offered = strings.Join(answer.LaneNames, ", ")
	}
	_, _ = fmt.Fprintln(w, terminalcontrol.Strip("No lane named "+strings.Join(unmatched, ", ")+"."))
	_, _ = fmt.Fprintln(w, terminalcontrol.Strip("  Configured sources offered: "+offered))
}
