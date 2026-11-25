package main

import (
	"errors"
	"fmt"
	"os"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newForecastCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "forecast",
		Short: "Your pursuits run forward, to show whether their paces fit your days",
		Long: "Your pursuits run forward, to show whether their paces fit your days.\n\n" +
			"A forecast runs the register, the document `doit pursuits edit` opens, through the real\n" +
			"draw, and stores what it predicted. Once enough time has passed, a trend grades that\n" +
			"against what you logged.",
		Example: "  doit forecast run    take a forecast now and store it\n" +
			"  doit forecast show   read the newest one back\n" +
			"  doit forecast trend  how earlier forecasts held up",
		GroupID: whatCameOfIt,
	})
	cmd.Annotations[portedWhole] = "true"
	cmd.AddCommand(newForecastRunCommand(), newForecastShowCommand(), newForecastListCommand(), newForecastTrendCommand())
	return cmd
}

func newForecastRunCommand() *cobra.Command {
	var budget int
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "run",
		Short: "Take a forecast from the register as it stands, and store it",
		Example: "  doit forecast run               at the daily budget the register declares\n" +
			"  doit forecast run --budget 120  what two hours a day would get done\n" +
			"  doit forecast run --json        the same reading as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var chosen *int
			if cmd.Flags().Changed("budget") {
				if budget < 1 {
					return goclikit.UsageError(errors.New("--budget takes a whole number of minutes above 0"))
				}
				chosen = &budget
			}
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, reading, err := client.TakeForecast(cmd.Context(), chosen)
			// The reading is stored once raw is back, and --json reads none
			// of its fields, so it prints what was stored either way.
			if asJSON && raw != nil {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			if err != nil {
				return err
			}
			return screens.Forecast(cmd.OutOrStdout(), reading, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().IntVar(&budget, "budget", 0, "Minutes a day to spend, overriding the register")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newForecastShowCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "show [timestamp]",
		Short: "Read a stored forecast back",
		Long: "Read a stored forecast back: the newest, or the one a prefix of its timestamp names.\n" +
			"`doit forecast list` lists each one's timestamp.",
		Example: "  doit forecast show          the newest forecast\n" +
			"  doit forecast show 2026-11  the one a timestamp starting that way names\n" +
			"  doit forecast show --json   the newest as data",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			handle := ""
			if len(args) == 1 {
				handle = args[0]
			}
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, reading, err := client.Forecast(cmd.Context(), handle)
			if err != nil {
				return err
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Forecast(cmd.OutOrStdout(), reading, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newForecastListCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "Every forecast taken, oldest first",
		Example: "  doit forecast list         every stored forecast and its timestamp, to pass to show\n" +
			"  doit forecast list --json  the same forecasts as data",
		Args: cobra.NoArgs,
		// With nothing stored it says so on stderr and exits 1, --json or not.
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, readings, err := client.Forecasts(cmd.Context())
			if err != nil {
				return err
			}
			if len(readings) == 0 {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "No forecast stored yet:  doit forecast run")
				return exitCode(1)
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Forecasts(cmd.OutOrStdout(), readings, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newForecastTrendCommand() *cobra.Command {
	var days int
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "trend",
		Short: "Grade the forecasts whose window has closed against the journal",
		Example: "  doit forecast trend            how forecasts held up at the service's default horizon\n" +
			"  doit forecast trend --days 30  how the month ahead held up\n" +
			"  doit forecast trend --json     the same verdicts as data",
		Args: cobra.NoArgs,
		// With no reading old enough to grade it says so on stderr and exits 1, --json or not.
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			var horizon *int
			if cmd.Flags().Changed("days") {
				horizon = &days
			}
			raw, trend, err := client.Trend(cmd.Context(), horizon)
			if err != nil {
				return err
			}
			if len(trend.Rows) == 0 {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "No reading is %d days old yet, so nothing can be graded:  doit forecast run\n", trend.Days)
				return exitCode(1)
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Trend(cmd.OutOrStdout(), trend, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().IntVar(&days, "days", 0, "Which horizon to grade, in days")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}
