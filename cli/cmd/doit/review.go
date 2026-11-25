package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/deskfiles"
	"github.com/datapointchris/doit/cli/internal/programs"
	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

// showBound is how long a due entry's `show` may run inside the shell nudge,
// so a slow command cannot hold the next shell's printout.
const showBound = 10 * time.Second

func newReviewCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "review",
		Short: "What's due to revisit, on a cadence",
		Long: "What's due to revisit, on a cadence.\n\n" +
			"A review item is something to revisit on a cadence, whether maintenance or practice. The\n" +
			"register is the document listing them, which `doit review edit` opens.",
		Example: "  doit review due                 what's due now, most overdue first\n" +
			"  doit review done prune-docker   mark one done, which sets when it comes due again\n" +
			"  doit review list                every item and its status, due or not",
		GroupID: whatYouDeclared,
	})
	cmd.AddCommand(
		newReviewDueCommand(), newReviewListCommand(), newReviewDoneCommand(), newReviewUndoCommand(),
		newReviewEditCommand(), newReviewNudgeEveryCommand(), newReviewNudgeCommand(),
	)
	return cmd
}

func newReviewDueCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "due",
		Short:   "What's due now, most overdue first",
		Example: "  doit review due  every item due, where the dashboard shows the first few",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			_, register, err := client.Review(cmd.Context())
			if err != nil {
				return err
			}
			return screens.ReviewDue(cmd.OutOrStdout(), register, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
}

func newReviewListCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "Every registered item and its status",
		Long: "Every registered item and its status.\n\n" +
			"In --json, `overdue` is days past due (negative is days until due) and null when the item\n" +
			"was never done, which counts as due; `due` says so directly.",
		Example: "  doit review list         every item and its status, due or not\n" +
			"  doit review list --json  the same items as data, to filter on `due` or `overdue`",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, register, err := client.Review(cmd.Context())
			if err != nil {
				return err
			}
			if asJSON {
				var whole struct {
					Entries json.RawMessage `json:"entries"`
				}
				if err := json.Unmarshal(raw, &whole); err != nil {
					return err
				}
				return printJSON(cmd.OutOrStdout(), whole.Entries)
			}
			return screens.ReviewList(cmd.OutOrStdout(), register, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newReviewDoneCommand() *cobra.Command {
	var on string
	cmd := &cobra.Command{
		Use:   "done <id>",
		Short: "Mark an item done, advancing its due date",
		Example: "  doit review done prune-docker                 done today\n" +
			"  doit review done prune-docker --on 2026-11-04  record one you did earlier, and read its due date from that day",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			day, err := pastDay(on, time.Now())
			if err != nil {
				return err
			}
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			done, err := client.CompleteReview(cmd.Context(), args[0], day)
			if err != nil {
				return refusedAsTyped(err)
			}
			after := ""
			switch {
			case on != "":
				after = readsNow(done.Status)
			case done.DueAgain() != "":
				after = " Due again in " + done.DueAgain() + "."
			}
			printReviewed(cmd.OutOrStdout(), "Marked", args[0], " done ("+done.DoneOn+")."+after)
			return nil
		},
	}
	cmd.Flags().StringVar(&on, "on", "", "The day it was done, YYYY-MM-DD, to record it late (today where omitted)")
	return cmd
}

// pastDay is --on as the day to record, or the desk's today where it was not
// passed. The desk's day is the one that counts, because the service's clock
// may sit in another zone, so a day after it is refused here.
func pastDay(on string, now time.Time) (string, error) {
	today := now.Format(time.DateOnly)
	if on == "" {
		return today, nil
	}
	day, err := time.Parse(time.DateOnly, on)
	if err != nil {
		return "", goclikit.UsageError(fmt.Errorf("--on: %q is not a day (YYYY-MM-DD)", on))
	}
	if day.Format(time.DateOnly) > today {
		return "", goclikit.UsageError(fmt.Errorf("--on: %s is later than today, and a completion records what has happened", on))
	}
	return day.Format(time.DateOnly), nil
}

// readsNow is how an entry reads after a correction, which need not sit one cadence from today.
func readsNow(status *service.ReviewStatus) string {
	if status == nil {
		return ""
	}
	last := ""
	if status.Last != nil && *status.Last != "" {
		last = ", last " + *status.Last
	}
	return " Now: " + status.Label + last + "."
}

// printReviewed prints a ✓ line naming the entry in yellow.
func printReviewed(out io.Writer, verb, id, rest string) {
	palette := gotermstyle.PaletteFor(os.Stdout)
	_, _ = fmt.Fprintln(out, palette.Paint(gotermstyle.CodeGreen, "✓")+" "+verb+" "+
		palette.Paint(gotermstyle.CodeYellow, terminalcontrol.Strip(id))+terminalcontrol.Strip(rest))
}

func newReviewUndoCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "undo <id>",
		Short:   "Take back an item's newest completion, so it reads as it did before",
		Example: "  doit review undo prune-docker  take back a completion recorded by mistake",
		Args:    cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx := cmd.Context()
			client, err := service.Open(ctx)
			if err != nil {
				return err
			}
			held, err := client.ReviewCompletions(ctx, args[0])
			if err != nil {
				return refusedAsTyped(err)
			}
			if len(held) == 0 {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "%s has no completion to take back.\n", terminalcontrol.Strip(args[0]))
				return exitCode(1)
			}
			undone, err := client.UndoReviewCompletion(ctx, args[0], held[len(held)-1].DoneOn)
			if err != nil {
				return refusedAsTyped(err)
			}
			printReviewed(cmd.OutOrStdout(), "Took back", args[0], " ("+undone.DoneOn+")."+readsNow(undone.Status))
			return nil
		},
	}
}

func newReviewEditCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "edit",
		Short:   "Edit the register in your editor, the document listing every review item",
		Example: "  doit review edit  add an item or change its cadence",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return editedOrExit(cmd, "review")
		},
	}
}

// editedOrExit edits one of the service's documents, exiting 1 where none stands after.
func editedOrExit(cmd *cobra.Command, name string) error {
	client, err := service.Open(cmd.Context())
	if err != nil {
		return err
	}
	stands, err := editDocument(cmd, client, name)
	if err != nil {
		return err
	}
	if !stands {
		return exitCode(1)
	}
	return nil
}

func newReviewNudgeEveryCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "nudge-every <duration>",
		Short: "Set how often the startup nudge may fire",
		Long: "Set how often the startup nudge may fire, on every machine.\n\n" +
			"The nudge is what a new shell prints about due items, once `doit shell init` is in your rc.",
		Example: "  doit review nudge-every 4h   at most every four hours, across every machine\n" +
			"  doit review nudge-every 90m  at most every ninety minutes",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			held, err := client.SetNudge(cmd.Context(), map[string]string{"every": args[0]})
			if err != nil {
				return refusedAsTyped(err)
			}
			keepNudgeInterval(cmd.ErrOrStderr(), held.IntervalMinutes)
			_, _ = fmt.Fprintf(cmd.OutOrStdout(), "Nudge interval set to %d minutes.\n", held.IntervalMinutes)
			return nil
		},
	}
}

// keepNudgeInterval writes the interval where the shell gate reads it.
func keepNudgeInterval(errOut io.Writer, minutes int) {
	if err := deskfiles.WriteNudgeInterval(minutes); err != nil {
		_, _ = fmt.Fprintln(errOut, "doit: the nudge interval the shell reads was not saved:", err)
	}
}

func newReviewNudgeCommand() *cobra.Command {
	return &cobra.Command{
		Use:    "nudge",
		Short:  "The startup nudge, called by the shell gate rather than by hand",
		Hidden: true,
		Args:   cobra.NoArgs,
		RunE:   func(cmd *cobra.Command, _ []string) error { return nudge(cmd) },
	}
}

// nudge prints what is due once the interval has passed since it last showed
// on any machine, and nothing when nothing is due. Each line collapses to the
// entry, how late it is and what settles it, because a nudge interrupts. After
// the roster, each due entry's `show` runs with its output inline.
func nudge(cmd *cobra.Command) error {
	ctx, out := cmd.Context(), cmd.OutOrStdout()
	client, err := service.Open(ctx)
	if err != nil {
		return err
	}
	held, err := client.Nudge(ctx)
	if err != nil {
		return err
	}
	keepNudgeInterval(cmd.ErrOrStderr(), held.IntervalMinutes)
	if !held.Show {
		return nil
	}
	if _, err := client.SetNudge(ctx, map[string]string{"last_nudged_at": time.Now().Format(time.RFC3339)}); err != nil {
		return err
	}
	if len(held.Due) == 0 {
		return nil
	}
	palette := gotermstyle.PaletteFor(os.Stdout)
	if err := screens.ReviewNudge(out, held, screens.TerminalWidths(), palette); err != nil {
		return err
	}
	for _, entry := range held.Due {
		ranShow(ctx, out, entry)
	}
	_, err = fmt.Fprintln(out, "Done:  "+palette.Paint(gotermstyle.CodeCyan, "doit review done <id>"))
	return err
}

// ranShow runs a due entry's `show` through sh, because a register entry is
// written by hand and may hold a pipeline. Its output prints inline, never
// captured or trimmed, and it is stopped after showBound.
func ranShow(ctx context.Context, out io.Writer, entry service.ReviewEntry) {
	if entry.Show == "" {
		return
	}
	err := programs.Program{Argv: []string{"sh", "-c", entry.Show}, Bound: showBound}.RunShowing(ctx, out)
	var failed *programs.Failed
	if errors.As(err, &failed) && failed.TimedOut {
		_, _ = fmt.Fprintf(out, "  %s: show stopped after %s\n", terminalcontrol.Strip(entry.ID), showBound)
	}
}
