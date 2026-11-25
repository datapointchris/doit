package main

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/service"
)

func newIngestionCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "ingestion",
		Short: "Decide the items that wait in ingestion on you",
		Long: "Decide the items that wait in ingestion on you.\n\n" +
			"ingestion takes in what outside sources send, and holds an item for you where no tag,\n" +
			"rule or confident guess placed it. The dashboard's ingestion-review lane lists them, each\n" +
			"with its key, and ingestion records each decision here as yours.",
		Example: "  doit ingestion items place reddit/t3_1h9xk2 life-articles  place one on a destination\n" +
			"  doit ingestion items drop reddit/t3_1hb0qa --cause subreddit  drop one, naming what decided it",
		GroupID: whatToDoNow,
	})
	cmd.AddCommand(newIngestionItemsCommand())
	return cmd
}

func newIngestionItemsCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "items",
		Short: "Place or drop an item that ingestion holds",
		Example: "  doit ingestion items place reddit/t3_1h9xk2 life-articles\n" +
			"  doit ingestion items drop reddit/t3_1hb0qa --cause subreddit",
	})
	cmd.AddCommand(newIngestionItemsPlaceCommand(), newIngestionItemsDropCommand())
	return cmd
}

// ingestionDecisionFlags are the flags that place and drop share.
type ingestionDecisionFlags struct {
	cause, note string
	asJSON      bool
}

func (f *ingestionDecisionFlags) bind(cmd *cobra.Command) {
	cmd.Flags().StringVar(&f.cause, "cause", "", "Name what decided it, such as subreddit or title")
	cmd.Flags().StringVar(&f.note, "note", "", "Add a note that ingestion records with the decision")
	cmd.Flags().BoolVar(&f.asJSON, "json", false, "Output ingestion's answer as JSON to stdout")
}

func (f *ingestionDecisionFlags) decided(key, destination string) service.IngestionDecided {
	decided := service.IngestionDecided{Key: key, Destination: destination}
	if f.cause != "" {
		decided.Cause = &f.cause
	}
	if f.note != "" {
		decided.Note = &f.note
	}
	return decided
}

func newIngestionItemsPlaceCommand() *cobra.Command {
	var flags ingestionDecisionFlags
	cmd := &cobra.Command{
		Use:   "place <key> <destination>",
		Short: "Place an item on a destination, as your decision",
		Long: "Place an item on a destination, as your decision.\n\n" +
			"The destination is one that ingestion declares, and a refusal names them.\n" +
			"`ingestion review place --help` says what a placement does to the item.",
		Example: "  doit ingestion items place reddit/t3_1h9xk2 life-articles\n" +
			"  doit ingestion items place reddit/t3_1h9xk2 life-articles --cause subreddit",
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, answer, err := client.PlaceIngestionItem(cmd.Context(), flags.decided(args[0], args[1]))
			return printIngestionDecisions(cmd, raw, answer, flags.asJSON, err)
		},
	}
	flags.bind(cmd)
	return cmd
}

func newIngestionItemsDropCommand() *cobra.Command {
	var flags ingestionDecisionFlags
	cmd := &cobra.Command{
		Use:   "drop <key>",
		Short: "Drop an item, as your decision",
		Long: "Drop an item, as your decision.\n\n" +
			"`ingestion review drop --help` says what a drop keeps.",
		Example: "  doit ingestion items drop reddit/t3_1hb0qa --cause subreddit",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, answer, err := client.DropIngestionItem(cmd.Context(), flags.decided(args[0], ""))
			return printIngestionDecisions(cmd, raw, answer, flags.asJSON, err)
		},
	}
	flags.bind(cmd)
	return cmd
}

// printIngestionDecisions prints the decisions ingestion made, or its answer
// whole. A key it does not hold, or a destination or cause it will
// not take, exits 2.
func printIngestionDecisions(cmd *cobra.Command, raw json.RawMessage, answer service.IngestionDecisions, asJSON bool, err error) error {
	if err != nil {
		return refusedAsTyped(err)
	}
	if asJSON {
		return printJSON(cmd.OutOrStdout(), raw)
	}
	_, err = fmt.Fprintln(cmd.OutOrStdout(), terminalcontrol.Strip(decidedSentence(answer)))
	return err
}

// decidedSentence names each decision in ingestion's answer by its gate and
// verdict, and the destination the first of them names.
func decidedSentence(a service.IngestionDecisions) string {
	on := ""
	parts := make([]string, 0, len(a.Decisions))
	for _, d := range a.Decisions {
		if d.Destination != nil && on == "" {
			on = " on " + *d.Destination
		}
		part := d.Gate + " " + d.Verdict
		if d.Lens != nil {
			part += " under the " + *d.Lens + " lens"
		}
		parts = append(parts, part)
	}
	return fmt.Sprintf("Decided %s%s: %s.", a.Key, on, strings.Join(parts, ", "))
}
