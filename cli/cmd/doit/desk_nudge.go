package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"syscall"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/desknudge"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newDeskNudgeCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "desk-nudge",
		Short: "The nudge when a long stretch at the desk runs past the interval",
		Long: "A nudge every interval at the desk, suggesting what `doit next` offers first.\n\n" +
			"The watch counts the time since the last nudge ended, then flashes its pill and chimes at\n" +
			"the interval. Your answers to it tune the interval, and every firing, answer and pick is\n" +
			"recorded for the warehouse.",
		Example: "  doit desk-nudge status           where the clock stands, and what the nudge suggests\n" +
			"  doit desk-nudge answer too_soon  answer the nudge that is showing\n" +
			"  doit desk-nudge pause            hold the clock until resumed",
		GroupID: thisMachine,
	})
	cmd.AddCommand(newWatchCommand(), newStatusCommand(), newAnswerCommand(), newPauseCommand(), newResumeCommand(), newPanelCommand())
	return cmd
}

func newWatchCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "watch",
		Short: "Count the time since the last nudge ended and fire the nudge at the interval, until stopped",
		Long: "Count the time since the last nudge ended and fire the nudge at the interval, until stopped.\n\n" +
			"The desk's user service runs this. It exits 1 where another watch already runs on this\n" +
			"desk, and 0 once its binary is replaced or it is stopped, after finishing what is under way.",
		Example: "  doit desk-nudge watch  run the clock in this terminal, where the desk has no user service running it",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			desknudge.SetLogger(desknudge.StderrHandler())
			ctx, stop := signal.NotifyContext(cmd.Context(), syscall.SIGTERM, os.Interrupt)
			defer stop()
			err := desknudge.NewWatch(desknudge.ThisDesk(version)).Run(ctx)
			if errors.Is(err, desknudge.ErrAnotherWatch) {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "doit desk-nudge:", err)
				return exitCode(1)
			}
			return err
		},
	}
}

func newStatusCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "status",
		Short: "The phase, the time left, the suggestion, the interval and the answer counts",
		Long:  "The phase, the time left, the suggestion, the interval and the answer counts, read from\nthis desk's state file with no request.",
		Example: "  doit desk-nudge status         where the clock stands, and what the nudge suggests\n" +
			"  doit desk-nudge status --json  the same as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			desk := desknudge.ThisDesk(version)
			status := desk.Status()
			if asJSON {
				return printJSON(cmd.OutOrStdout(), status)
			}
			status.Print(cmd.OutOrStdout(), styler(cmd.OutOrStdout()), desk.LabelOf)
			return nil
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newAnswerCommand() *cobra.Command {
	var nudgeID string
	var last bool
	cmd := &cobra.Command{
		Use:   "answer <answer>",
		Short: "Answer a nudge, which ends it and restarts the clock",
		Long: "Answer a nudge, which ends it and restarts the clock. The answer is one of\n" +
			fmt.Sprint(desknudge.Answers()) + ".\n\n" +
			"good_timing, too_soon and too_late say when it came, and a nudge holds one of them. A\n" +
			"second replaces the first, which the service keeps in its history, so --last changes a\n" +
			"misclicked answer. bothered is said apart from them.\n\n" +
			"With no nudge ready, too_late calls one now and answers it: you were ready before it\n" +
			"fired. It restarts the clock, and every other answer needs --nudge or --last.\n\n" +
			"Exits 0 once the service stores the answer. Exits 4 where no answer came, and 1 where the\n" +
			"login or the service failed: the answer is then kept on this desk, and the watch sends it\n" +
			"again. Exits 1 where the service refuses the answer itself, which keeps nothing. Exits 2\n" +
			"where there is no nudge to answer.",
		Example: "  doit desk-nudge answer too_soon              the ready nudge came too soon, so the interval grows\n" +
			"  doit desk-nudge answer too_late              with none ready, you were ready before it fired\n" +
			"  doit desk-nudge answer too_late --last        change the newest nudge's answer to too late\n" +
			"  doit desk-nudge answer bothered --nudge <id>  answer a nudge that has already ended",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !desknudge.IsAnswer(args[0]) {
				return goclikit.UsageError(fmt.Errorf("%q is not an answer; give one of %v", args[0], desknudge.Answers()))
			}
			desk := desknudge.ThisDesk(version)
			if last {
				if nudgeID = desk.NewestNudge(); nudgeID == "" {
					return goclikit.UsageError(fmt.Errorf("--last: %w: this desk remembers no nudge", desknudge.ErrNothingToAnswer))
				}
			}
			answered, err := desk.Answer(cmd.Context(), args[0], nudgeID)
			if errors.Is(err, desknudge.ErrNothingToAnswer) {
				return goclikit.UsageError(fmt.Errorf("--nudge: %w", err))
			}
			if err != nil {
				return err
			}
			code, said := answered.Outcome()
			if code != 0 {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "doit desk-nudge:", said)
				return exitCode(code)
			}
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), said)
			return nil
		},
	}
	cmd.Flags().StringVar(&nudgeID, "nudge", "", "The nudge to answer, where it is not the ready one")
	cmd.Flags().BoolVar(&last, "last", false, "Answer this desk's newest nudge, to change a misclicked answer")
	cmd.MarkFlagsMutuallyExclusive("nudge", "last")
	return cmd
}

func newPauseCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "pause",
		Short:   "Hold the clock where it stands, ending a nudge that is showing, until resume",
		Example: "  doit desk-nudge pause  hold the clock until resume",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			paused, err := desknudge.ThisDesk(version).Pause()
			if err != nil {
				return err
			}
			if paused {
				_, _ = fmt.Fprintln(cmd.OutOrStdout(), "The desk nudge is paused. `doit desk-nudge resume` carries on the count.")
			} else {
				_, _ = fmt.Fprintln(cmd.OutOrStdout(), "The desk nudge was already paused.")
			}
			return nil
		},
	}
}

func newResumeCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "resume",
		Short:   "Carry on the count from where pause held it",
		Example: "  doit desk-nudge resume  carry on the count from where pause held it",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			resumed, err := desknudge.ThisDesk(version).Resume()
			if err != nil {
				return err
			}
			if resumed {
				_, _ = fmt.Fprintln(cmd.OutOrStdout(), "The desk nudge carries on the count.")
			} else {
				_, _ = fmt.Fprintln(cmd.OutOrStdout(), "The desk nudge was not paused.")
			}
			return nil
		},
	}
}

func newPanelCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "panel",
		Short: "The desk nudge's panel, which completes any of today's rows in one pick",
		Long: "The desk nudge's panel, which completes any of today's rows in one pick.\n\n" +
			"The watch reads the service's desk panel view and keeps it on this desk. `panel open`\n" +
			"shows it as a page in a window, where a click completes a row at once. `panel show`\n" +
			"prints it with the key that picks each row, and `panel pick` hands a key to the watch,\n" +
			"which completes the row it names at once and prints the next step.",
		Example: "  doit desk-nudge panel open           today's rows as a page in a window\n" +
			"  doit desk-nudge panel show           today's rows with the key that picks each\n" +
			"  doit desk-nudge panel pick habit:17  complete habit 17 through life",
	})
	cmd.AddCommand(newPanelOpenCommand(), newPanelShowCommand(), newPanelPickCommand())
	return cmd
}

// The panel's window. A second open of the same name raises it, and a Hyprland
// window rule and the events file's focused window see its title.
const (
	panelWindowName  = "doit-panel"
	panelWindowTitle = "doit panel"
)

// panelWindowArgv is webviewrs showing the panel page at url, closed by the
// page's Esc or once the window loses focus.
func panelWindowArgv(url string) []string {
	return []string{"webviewrs", "-n", panelWindowName, "-t", panelWindowTitle, "--dark", "--close-on-blur", url}
}

func newPanelOpenCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "open",
		Short: "Show today's rows as a page in a window, where a click completes a row",
		Long: "Show today's rows as a page in a window, where a click completes a row at once. The\n" +
			"watch serves the page, and webviewrs draws the window, which closes on Esc or once it\n" +
			"loses focus. A second open raises the window that is already open.\n\n" +
			"Replaces itself with webviewrs once the watch gives the page's address. Exits 4 where no\n" +
			"watch answers. Exits 1 where the watch answers and serves no page, or webviewrs is not\n" +
			"installed. A failure at the watch also shows a notification, because a pill or a key\n" +
			"shows no stderr.",
		Example: "  doit desk-nudge panel open  today's rows as a page in a window",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			webviewrs, err := exec.LookPath("webviewrs")
			if err != nil {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(),
					"doit desk-nudge panel open: webviewrs is not on PATH, and it draws the panel's window. Install webviewrs, or read the rows with `doit desk-nudge panel show`.")
				return exitCode(1)
			}
			minted, err := desknudge.DeskPaths().AskPageCode(cmd.Context())
			if err == nil {
				return syscall.Exec(webviewrs, panelWindowArgv(minted.URL), os.Environ())
			}
			// Exit 4 is kept for no answer, which the same command run later can outlast.
			const olderWatch = "the desk nudge watch running is an older doit's, which serves no panel page. Restarting the desk-nudge user service starts this doit's watch."
			said, code := err.Error(), desknudge.PickFailed
			var refusal *desknudge.PickError
			var unanswered *service.NoAnswer
			switch {
			case errors.Is(err, desknudge.ErrNoWatch):
				said, code = "no desk nudge watch is running. Start the desk-nudge user service, which runs `doit desk-nudge watch`, or run it in a terminal.", service.NoAnswerExit
			case errors.Is(err, desknudge.ErrWatchServesNoPicks):
				said, code = olderWatch, service.NoAnswerExit
			case errors.Is(err, desknudge.ErrWatchServesNoPage):
				said = olderWatch
			case errors.As(err, &refusal):
				code = refusal.ExitCode
			case errors.As(err, &unanswered):
				code = service.NoAnswerExit
			}
			printed := "doit desk-nudge panel open: " + said
			if said != err.Error() {
				printed += "\n  (" + err.Error() + ")"
			}
			_, _ = fmt.Fprintln(cmd.ErrOrStderr(), printed)
			desknudge.Notify(cmd.Context(), "doit: the panel did not open", said)
			return exitCode(code)
		},
	}
}

func newPanelShowCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "show",
		Short: "The suggestion, the answers while a nudge is ready, and every row that something can complete",
		Long: "The suggestion, the four answers while a nudge is ready, and every row that something\n" +
			"can complete. Read with no request from the parts the watch keeps. A row picked here\n" +
			"since they were read is left out, and a row clicked on the panel page says how its\n" +
			"pick stands. Exits 0.",
		Example: "  doit desk-nudge panel show         today's rows with the key that picks each\n" +
			"  doit desk-nudge panel show --json  the step as data, for a board to draw",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return printStep(cmd.OutOrStdout(), desknudge.ThisDesk(version).Show(), asJSON)
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output the step as JSON to stdout")
	return cmd
}

func newPanelPickCommand() *cobra.Command {
	var asJSON bool
	var minutes int
	var note string
	cmd := &cobra.Command{
		Use:   "pick <key>",
		Short: "Act on one row: answer the nudge, open a timed pursuit's minutes, or complete the row",
		Long: "Act on one row: answer the nudge, open a timed pursuit's minutes, or complete the row\n" +
			"through its owner. The watch runs the pick, so it needs the desk nudge watch.\n\n" +
			"Prints the next step, or a done step once something is completed, and exits 0 for\n" +
			"either. A timed pursuit opens its minutes, unless --minutes names them. --note says what\n" +
			"a pursuit's log was, as `doit pursuits log` takes one, and is refused on any other pick.\n" +
			"Exits 2 where the panel will not act on the key, such as a row picked already, and\n" +
			"nothing ran. Exits 1 where the completion did not go through, and 4 where the service\n" +
			"or the watch gave no answer. A notification names the error in each case, because a\n" +
			"board shows no stderr.",
		Example: "  doit desk-nudge panel pick pursuit:read               open read's minutes, starting on its usual amount\n" +
			"  doit desk-nudge panel pick pursuit:read --minutes 40  log 40 minutes of read\n" +
			"  doit desk-nudge panel pick pursuit:chore --note 'vacuumed the house'  log a chore, saying which\n" +
			"  doit desk-nudge panel pick review:prune-docker --json mark a review entry done, and print the done step as data",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var logged *int
			if cmd.Flags().Changed("minutes") {
				if minutes < 1 {
					return goclikit.UsageError(errors.New("--minutes takes a whole number of minutes above 0"))
				}
				logged = &minutes
			}
			step, err := desknudge.DeskPaths().AskPick(cmd.Context(), args[0], logged, note)
			if said, code, ok := desknudge.PickFailure(err); ok {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "doit desk-nudge panel pick:", said)
				return exitCode(code)
			}
			if err != nil {
				return err
			}
			if step.Step == desknudge.StepMinutes && noInput {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "doit desk-nudge panel pick: %s is counted in minutes, so --minutes names them\n", args[0])
				return exitCode(desknudge.PickRefused)
			}
			return printStep(cmd.OutOrStdout(), step, asJSON)
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output the next step as JSON to stdout")
	cmd.Flags().IntVar(&minutes, "minutes", 0, "Log a timed pursuit's minutes at once, rather than opening its minutes step")
	cmd.Flags().StringVar(&note, "note", "", "Say what a pursuit's log was, such as which chore")
	return cmd
}

// printStep writes a step as JSON, or as its message and then each row with the key that picks it.
func printStep(out io.Writer, step desknudge.Panel, asJSON bool) error {
	if asJSON {
		return printJSON(out, step)
	}
	if step.Message != nil {
		_, _ = fmt.Fprintln(out, *step.Message)
	}
	rows := step.Rows
	width := 0
	for _, row := range rows {
		if row.Key != nil {
			width = max(width, len(*row.Key))
		}
	}
	style := styler(out)
	for _, row := range rows {
		key := ""
		if row.Key != nil {
			key = *row.Key
		}
		text := row.Text
		if len(row.Picks) > 0 {
			text += " · " + row.Picks[len(row.Picks)-1].State
		}
		if row.Kind == desknudge.KindNote {
			text = style(text, "yellow")
		}
		_, _ = fmt.Fprintf(out, "  %-*s  %s\n", width, key, text)
	}
	return nil
}

func printJSON(out io.Writer, document any) error {
	encoder := json.NewEncoder(out)
	encoder.SetIndent("", "  ")
	encoder.SetEscapeHTML(false)
	return encoder.Encode(document)
}

// styler colors text for out where out renders color.
func styler(out io.Writer) func(text, color string) string {
	enabled := gotermstyle.Enabled(out)
	return func(text, color string) string {
		if !enabled || color != "yellow" {
			return text
		}
		return gotermstyle.CodeYellow + text + gotermstyle.CodeReset
	}
}
