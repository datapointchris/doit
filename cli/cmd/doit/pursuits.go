package main

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/actor"
	"github.com/datapointchris/doit/cli/internal/deskfiles"
	"github.com/datapointchris/doit/cli/internal/pursuits"
	"github.com/datapointchris/doit/cli/internal/screens"
	"github.com/datapointchris/doit/cli/internal/service"
)

func newPursuitsCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "pursuits",
		Short: "What `doit next` draws from: each pursuit, its goal and its weight",
		Long: "What `doit next` draws from: each pursuit, its goal and its weight. Bare `doit pursuits`\n" +
			"lists them, as `doit pursuits list` does.\n\n" +
			"A pursuit is something you mean to keep doing, at a pace you declare. Its weight orders\n" +
			"what is owed and picks the rest of the draw. The register is the document holding them,\n" +
			"which `doit pursuits edit` opens.",
		Example: "  doit pursuits list                   every pursuit and when it is next due\n" +
			"  doit pursuits log read --minutes 40  record a 40-minute sitting of a timed pursuit\n" +
			"  doit pursuits drift                  what each goal asked for against what you did",
		GroupID: whatYouDeclared,
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return listPursuits(cmd, false)
		},
	})
	cmd.Annotations[portedWhole] = "true"
	cmd.AddCommand(
		newPursuitsListCommand(), newPursuitsLogCommand(), newPursuitsSkipCommand(), newPursuitsResumeCommand(),
		newPursuitsResetCommand(), newPursuitsDriftCommand(), newPursuitsDormantCommand(), newPursuitsEvidenceCommand(),
		newPursuitsEditCommand(), newPursuitsNamesCommand(),
	)
	return cmd
}

func newPursuitsListCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "Every pursuit, its weight, goal and when it is next due",
		Example: "  doit pursuits list         every pursuit, its weight, goal and when it is next due\n" +
			"  doit pursuits list --json  the same pursuits and the state behind them, as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error { return listPursuits(cmd, asJSON) },
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

// listPursuits prints every pursuit, and rewrites the names the shell completes.
func listPursuits(cmd *cobra.Command, asJSON bool) error {
	client, err := service.Open(cmd.Context())
	if err != nil {
		return err
	}
	raw, list, err := client.PursuitList(cmd.Context())
	if err != nil {
		return err
	}
	if asJSON {
		return printJSON(cmd.OutOrStdout(), raw)
	}
	saveCompletionNames(cmd.ErrOrStderr(), list.Pursuits)
	offers, err := client.Offers(cmd.Context())
	if err != nil {
		return err
	}
	return screens.PursuitList(cmd.OutOrStdout(), list, offers, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
}

// saveCompletionNames rewrites the names file that the zsh completion reads.
func saveCompletionNames(errOut io.Writer, register map[string]service.RegisterEntry) {
	descriptions := make(map[string]any, len(register))
	for name, entry := range register {
		descriptions[name] = entry.Description
	}
	if err := deskfiles.WriteCompletionNames(descriptions); err != nil {
		_, _ = fmt.Fprintln(errOut, "doit: the names the shell completes were not saved:", err)
	}
}

func newPursuitsLogCommand() *cobra.Command {
	var ago, item string
	var minutes int
	var assumeYes, noWrite bool
	cmd := &cobra.Command{
		Use:   "log [pursuit] [note...]",
		Short: "Record having done one, writing through to the app that owns it",
		Long: "Record having done one, writing through to the app that owns it.\n\n" +
			"Pass every field to log in one line. Leave one out at a terminal and it is asked for,\n" +
			"with the pursuits listed and the current draw marked. A field already passed is never\n" +
			"asked about, and --no-input skips every question.",
		Example: "  doit pursuits log                                  pick the pursuit and answer what it asks\n" +
			"  doit pursuits log read --minutes 40 chapter three  all of it in one line, with a note\n" +
			"  doit pursuits log read --ago 3h --minutes 40       something you did earlier\n" +
			"  doit pursuits log tasks --item 532 --yes           complete the drawn task 532 with no question asked\n" +
			"  doit pursuits log chore --no-write                 record it without running the pursuit's on_log command",
		Args: cobra.ArbitraryArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			wanted := logWanted{assumeYes: assumeYes, noWrite: noWrite, occurred: time.Now()}
			if cmd.Flags().Changed("minutes") {
				if minutes < 1 {
					return goclikit.UsageError(errors.New("--minutes takes a whole number of minutes above 0"))
				}
				wanted.minutes = &minutes
			}
			if cmd.Flags().Changed("item") {
				wanted.itemID = &item
			}
			if ago != "" {
				back, ok := span(ago)
				if !ok {
					return goclikit.UsageError(fmt.Errorf("--ago takes a span such as 30s, 90m, 3h, 2d or 1w, not %q", ago))
				}
				wanted.occurred = wanted.occurred.Add(-back)
			}
			if len(args) > 0 {
				wanted.name, wanted.words = args[0], args[1:]
			}
			return logPursuit(cmd, wanted)
		},
	}
	cmd.Flags().StringVar(&ago, "ago", "", "Log something you did earlier: a span such as 30s, 90m, 3h, 2d or 1w")
	cmd.Flags().IntVar(&minutes, "minutes", 0, "How long it took, in whole minutes (asked for when a pursuit is measured in time)")
	cmd.Flags().BoolVarP(&assumeYes, "yes", "y", false, "Run the pursuit's on_log command without asking")
	cmd.Flags().BoolVar(&noWrite, "no-write", false, "Log only; never touch the owning app")
	cmd.Flags().StringVar(&item, "item", "", "The id of the choice you did, among the pursuit's current ones. Refused, naming the ids "+
		"it holds, where none carries it. Without it, several choices are asked about at a terminal and none is named otherwise")
	return cmd
}

// spanPattern is a span such as 30m or 2w, as every flag that takes one reads it.
var spanPattern = regexp.MustCompile(`^(\d+)([smhdw])$`)

// span is text as a duration, where it is a span.
func span(text string) (time.Duration, bool) {
	found := spanPattern.FindStringSubmatch(text)
	if found == nil {
		return 0, false
	}
	count, err := strconv.Atoi(found[1])
	if err != nil {
		return 0, false
	}
	unit := map[string]time.Duration{"s": time.Second, "m": time.Minute, "h": time.Hour, "d": 24 * time.Hour, "w": 7 * 24 * time.Hour}[found[2]]
	return time.Duration(count) * unit, true
}

// logWanted is what `doit pursuits log` was given.
type logWanted struct {
	name      string
	words     []string
	minutes   *int
	itemID    *string
	occurred  time.Time
	assumeYes bool
	noWrite   bool
}

// logPursuit logs one pursuit, asking for whatever was not passed where
// someone can answer. The service's check matches the name and refuses bad
// minutes or items first, so nothing runs or is written before it passes.
func logPursuit(cmd *cobra.Command, wanted logWanted) error {
	ctx, out, errOut := cmd.Context(), cmd.OutOrStdout(), cmd.ErrOrStderr()
	client, err := service.Open(ctx)
	if err != nil {
		return err
	}
	check, err := checkedPursuit(cmd, client, wanted, "log")
	if errors.Is(err, errAbandoned) {
		_, _ = fmt.Fprintln(errOut, "  Nothing logged.")
		return exitCode(1)
	}
	if err != nil {
		return err
	}
	note := strings.Join(wanted.words, " ")
	if len(wanted.words) == 0 && canAsk() {
		if note, err = asked(out, "  note, or Enter to skip: "); err != nil {
			_, _ = fmt.Fprintln(errOut, "  Nothing logged.")
			return exitCode(1)
		}
	}
	if wanted.minutes == nil && check.MinutesRefusal != nil {
		if !canAsk() {
			return goclikit.UsageError(fmt.Errorf("%s: pass --minutes <n>", *check.MinutesRefusal))
		}
		if wanted.minutes, err = askedMinutes(out, errOut); err != nil {
			_, _ = fmt.Fprintln(errOut, "  Nothing logged.")
			return exitCode(1)
		}
	}
	chosen := check.Chosen
	if check.Ask && canAsk() {
		chosen = askedChoice(out, check.Choices)
	}
	var downstream map[string]any
	if !wanted.noWrite {
		if downstream, err = ranOnLog(cmd, check, chosen, note, wanted); err != nil {
			return err
		}
	}
	entry := pursuits.Entry{
		ID: service.NewWriteID(time.Now()), Pursuit: check.Pursuit, Occurred: wanted.occurred, Note: note,
		Minutes: wanted.minutes, Machine: actor.MachineName(), Actor: actor.Identity(),
	}
	body := entry.Body(check.NamesChosen, chosen, downstream)
	logged, err := pursuits.Posted(ctx, client, body)
	if err != nil {
		if pursuits.Acted(downstream) {
			ran, repair := pursuits.RepairLines(body)[0], pursuits.RepairLines(body)[1]
			_, _ = fmt.Fprintln(errOut, yellow(errOut, "  "+terminalcontrol.Strip(ran)))
			_, _ = fmt.Fprintln(errOut, "  "+terminalcontrol.Strip(repair))
		}
		return err
	}
	palette := gotermstyle.PaletteFor(os.Stdout)
	_, _ = fmt.Fprintln(out, palette.Paint(gotermstyle.CodeGreen, "Logged")+" "+terminalcontrol.Strip(pursuits.Line(body, logged)))
	if pursuits.Acted(downstream) {
		_, _ = fmt.Fprintln(out, palette.Paint(gotermstyle.CodeGreen, "Ran")+" "+terminalcontrol.Strip(fmt.Sprint(downstream["command"])))
	}
	return nil
}

// checkedPursuit is the service's check of the pursuit wanted, asking which
// pursuit where none was named. A name typed at the question that matches no
// one pursuit is said and asked again. verb names the command, for the line
// that says how to name the pursuit with no terminal.
func checkedPursuit(cmd *cobra.Command, client *service.Client, wanted logWanted, verb string) (pursuits.Check, error) {
	ctx, out, errOut := cmd.Context(), cmd.OutOrStdout(), cmd.ErrOrStderr()
	name, prompted := wanted.name, false
	for {
		if name == "" {
			if !canAsk() {
				_, _ = fmt.Fprintf(errOut, "Which pursuit? Pass it as the argument:  doit pursuits %s <pursuit>\n", verb)
				return pursuits.Check{}, exitCode(1)
			}
			if !prompted {
				register, err := client.Register(ctx)
				if err != nil {
					return pursuits.Check{}, err
				}
				listForPrompt(out, register.PromptRows, register.Offered)
				prompted = true
			}
			answer, err := asked(out, "\n  pursuit: ")
			if err != nil || answer == "" {
				return pursuits.Check{}, errAbandoned
			}
			name = answer
		}
		check, err := pursuits.Checked(ctx, client, name, wanted.minutes, wanted.itemID)
		var refused *service.Refused
		switch {
		case errors.As(err, &refused) && refused.Status == http.StatusNotFound && prompted:
			_, _ = fmt.Fprintln(errOut, "  "+terminalcontrol.Strip(refused.Message))
			name = ""
			continue
		case errors.As(err, &refused) && refused.Status == http.StatusNotFound:
			_, _ = fmt.Fprintln(errOut, "doit: "+terminalcontrol.Strip(refused.Message)+". See:  doit pursuits list")
			return check, exitCode(2)
		case errors.As(err, &refused) && refused.Status == http.StatusUnprocessableEntity:
			return check, goclikit.UsageError(err)
		}
		return check, err
	}
}

// listForPrompt lists the pursuits that "Which pursuit?" offers, heaviest
// first, and marks the draw still on screen.
func listForPrompt(out io.Writer, rows []service.PromptRow, offered []string) {
	palette := gotermstyle.PaletteFor(os.Stdout)
	width := 0
	for _, row := range rows {
		width = max(width, len([]rune(row.Name)))
	}
	_, _ = fmt.Fprintln(out)
	for _, row := range rows {
		mark, code := " ", "\033[2m"
		for _, name := range offered {
			if name == row.Name {
				mark, code = "›", gotermstyle.CodeWhite
			}
		}
		name := terminalcontrol.Strip(row.Name)
		_, _ = fmt.Fprintf(out, "  %s %s  %s\n", palette.Paint(gotermstyle.CodeCyan, mark),
			palette.Paint(code, name+strings.Repeat(" ", max(0, width-len([]rune(name))))),
			palette.Paint("\033[2m", terminalcontrol.Strip(row.Summary)))
	}
}

// askedMinutes asks how long it took until a whole number above 0 is typed.
// It offers no default, because Enter would write the register's checkoff
// size into the journal as a measurement.
func askedMinutes(out, errOut io.Writer) (*int, error) {
	for {
		answer, err := asked(out, "  minutes: ")
		if err != nil {
			return nil, err
		}
		if minutes, err := strconv.Atoi(answer); err == nil && minutes > 0 {
			return &minutes, nil
		}
		_, _ = fmt.Fprintln(errOut, "  A whole number of minutes.")
	}
}

// askedChoice asks which of several choices was done. Enter means none of
// them, because the first would complete whatever happened to sort first.
func askedChoice(out io.Writer, choices []pursuits.Choice) map[string]any {
	palette := gotermstyle.PaletteFor(os.Stdout)
	_, _ = fmt.Fprintln(out)
	for number, choice := range choices {
		_, _ = fmt.Fprintf(out, "   %s%s\n", palette.Paint(gotermstyle.CodeCyan, strconv.Itoa(number+1)+"  "), terminalcontrol.Strip(choice.Line))
	}
	answer, err := asked(out, "\n  which one, or Enter for none: ")
	if number, convErr := strconv.Atoi(answer); err == nil && convErr == nil && number >= 1 && number <= len(choices) {
		return choices[number-1].Item
	}
	return nil
}

// ranOnLog runs the pursuit's on_log against chosen after asking, and answers
// what it did. With --yes it runs unasked. With nobody to ask it runs nothing,
// because the choice may be a quarter of an hour old, and a wrong completion
// is a silent lie in another app's data.
func ranOnLog(cmd *cobra.Command, check pursuits.Check, chosen map[string]any, note string, wanted logWanted) (map[string]any, error) {
	onLog, err := pursuits.OnLogFor(check.OnLog, chosen, note, wanted.minutes)
	if err != nil || onLog == nil {
		return nil, err
	}
	if !wanted.assumeYes {
		if !canAsk() {
			return nil, nil
		}
		palette := gotermstyle.PaletteFor(os.Stdout)
		if !confirmed(cmd.OutOrStdout(), "  "+palette.Paint(gotermstyle.CodeCyan, terminalcontrol.Strip(onLog.Command))+"  run it? [y/N] ") {
			return onLog.Declined(), nil
		}
	}
	downstream, err := onLog.Ran(cmd.Context())
	if failure := pursuits.Failure(downstream); err == nil && failure != "" {
		errOut := cmd.ErrOrStderr()
		_, _ = fmt.Fprintln(errOut, yellow(errOut, "  "+terminalcontrol.Strip(onLog.Command)+" "+failure))
	}
	return downstream, err
}

func newPursuitsSkipCommand() *cobra.Command {
	var duration string
	cmd := &cobra.Command{
		Use:   "skip [pursuit]",
		Short: "Pass on one — out of the draw until it expires, owing nothing meanwhile",
		Example: "  doit pursuits skip                pick from what is in the draw, and pass for one interval\n" +
			"  doit pursuits skip chore --for 2w  out of the draw for two weeks, owing nothing meanwhile",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, out, errOut := cmd.Context(), cmd.OutOrStdout(), cmd.ErrOrStderr()
			client, err := service.Open(ctx)
			if err != nil {
				return err
			}
			var name string
			if len(args) > 0 {
				name = args[0]
			} else {
				if !canAsk() {
					_, _ = fmt.Fprintln(errOut, "Which pursuit? Pass it as the argument:  doit pursuits skip <pursuit>")
					return exitCode(1)
				}
				register, err := client.Register(ctx)
				if err != nil {
					return err
				}
				var drawn []service.PromptRow
				for _, row := range register.PromptRows {
					for _, active := range register.Active {
						if row.Name == active {
							drawn = append(drawn, row)
						}
					}
				}
				if len(drawn) == 0 {
					_, _ = fmt.Fprintln(errOut, "Nothing is in the draw to pass.")
					return exitCode(1)
				}
				listForPrompt(out, drawn, register.Offered)
				if name, err = asked(out, "\n  pursuit: "); err != nil || name == "" {
					_, _ = fmt.Fprintln(errOut, "  Nothing skipped.")
					return exitCode(1)
				}
			}
			body := map[string]any{
				"id": service.NewWriteID(time.Now()), "pursuit": name, "machine": actor.MachineName(), "actor": actor.Identity(),
			}
			if duration != "" {
				body["for"] = duration
			}
			var passed struct {
				Days  int    `json:"days"`
				Until string `json:"until"`
				Entry struct {
					Pursuit string `json:"pursuit"`
				} `json:"entry"`
			}
			if err := client.Post(ctx, "/journal/skip", body, true, &passed); err != nil {
				return refusedAsTyped(err)
			}
			until := passed.Until
			if at, err := time.Parse(time.RFC3339Nano, passed.Until); err == nil {
				until = at.Format("02 Jan 2006")
			}
			palette := gotermstyle.PaletteFor(os.Stdout)
			matched := terminalcontrol.Strip(passed.Entry.Pursuit)
			_, _ = fmt.Fprintf(out, "%s %s — out of the draw for %dd, until %s. Owes nothing meanwhile, weight untouched.\n",
				palette.Paint(gotermstyle.CodeYellow, "Passed"), matched, passed.Days, until)
			_, _ = fmt.Fprintf(out, "  Back sooner:  %s\n", palette.Paint(gotermstyle.CodeCyan, "doit pursuits resume "+matched))
			return nil
		},
	}
	cmd.Flags().StringVar(&duration, "for", "", "How long to pass for, in days or weeks: 3d or 2w. One interval when omitted")
	return cmd
}

// refusedAsTyped turns the service's refusal of what was typed into a usage
// error, which exits 2. The service answers 404 for a name that means no one
// pursuit, and 422 for a value it cannot read.
func refusedAsTyped(err error) error {
	var refused *service.Refused
	if errors.As(err, &refused) && (refused.Status == http.StatusNotFound || refused.Status == http.StatusUnprocessableEntity) {
		return goclikit.UsageError(err)
	}
	return err
}

func newPursuitsResumeCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "resume [pursuit]",
		Short: "End a standing skip, so the pursuit returns to the next draw",
		Example: "  doit pursuits resume chore  end one skip early\n" +
			"  doit pursuits resume        end every skip",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			body := map[string]any{"pursuit": nil, "machine": actor.MachineName(), "actor": actor.Identity()}
			if len(args) > 0 {
				body["pursuit"] = args[0]
			}
			var resumed struct {
				Resumed []string `json:"resumed"`
			}
			if err := client.Post(cmd.Context(), "/journal/resume", body, false, &resumed); err != nil {
				return refusedAsTyped(err)
			}
			out := cmd.OutOrStdout()
			if len(resumed.Resumed) == 0 {
				if len(args) > 0 {
					_, _ = fmt.Fprintf(out, "%s is not skipped, and is already in the draw.\n", terminalcontrol.Strip(args[0]))
				} else {
					_, _ = fmt.Fprintln(out, "Nothing is skipped.")
				}
				return nil
			}
			palette := gotermstyle.PaletteFor(os.Stdout)
			_, _ = fmt.Fprintf(out, "%s %s — back in the next draw.\n", palette.Paint(gotermstyle.CodeGreen, "Resumed"),
				terminalcontrol.Strip(strings.Join(resumed.Resumed, ", ")))
			return nil
		},
	}
}

func newPursuitsResetCommand() *cobra.Command {
	var assumeYes bool
	cmd := &cobra.Command{
		Use:   "reset [pursuit]",
		Short: "Start a balance again from now; the journal keeps its history",
		Long: "Start a balance again from now: one pursuit, or every one when none is named. The journal\n" +
			"keeps its history, because a reset is a marker written into it.\n\n" +
			"Resetting every pursuit is confirmed first, because it discards every balance at once.",
		Example: "  doit pursuits reset chore  forgive what one pursuit owes, starting its balance again from now\n" +
			"  doit pursuits reset        every pursuit, after asking\n" +
			"  doit pursuits reset --yes  every pursuit, without asking",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ctx, out, errOut := cmd.Context(), cmd.OutOrStdout(), cmd.ErrOrStderr()
			client, err := service.Open(ctx)
			if err != nil {
				return err
			}
			body := map[string]any{"pursuits": nil, "machine": actor.MachineName(), "actor": actor.Identity()}
			if len(args) > 0 {
				body["pursuits"] = []string{args[0]}
			} else if !assumeYes {
				register, err := client.Register(ctx)
				if err != nil {
					return err
				}
				count := len(register.Pursuits)
				if !canAsk() {
					_, _ = fmt.Fprintf(errOut, "Resetting all %d pursuits discards every balance. Pass --yes to do it unattended.\n", count)
					_, _ = fmt.Fprintln(errOut, "  Nothing reset.")
					return exitCode(1)
				}
				palette := gotermstyle.PaletteFor(os.Stdout)
				if !confirmed(out, "  "+palette.Paint(gotermstyle.CodeYellow, fmt.Sprintf("Reset all %d pursuits", count))+
					" — every accrued balance is discarded. [y/N] ") {
					_, _ = fmt.Fprintln(errOut, "  Nothing reset.")
					return exitCode(1)
				}
			}
			var reset struct {
				Reset []string `json:"reset"`
			}
			if err := client.Post(ctx, "/journal/reset", body, false, &reset); err != nil {
				return refusedAsTyped(err)
			}
			balances := "the balance"
			if len(reset.Reset) > 1 {
				balances = "each balance"
			}
			palette := gotermstyle.PaletteFor(os.Stdout)
			_, _ = fmt.Fprintf(out, "%s %s — %s starts again from now.\n", palette.Paint(gotermstyle.CodeGreen, "Reset"),
				terminalcontrol.Strip(strings.Join(reset.Reset, ", ")), balances)
			return nil
		},
	}
	cmd.Flags().BoolVarP(&assumeYes, "yes", "y", false, "Zero every pursuit without confirming")
	return cmd
}

func newPursuitsDriftCommand() *cobra.Command {
	var since, until string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "drift",
		Short: "What each goal asked for against what you did",
		Long: "What each goal asked for over a window against what got done, in each pursuit's own unit.\n\n" +
			"It never adjusts a goal. Offered counts sit beside them, which tell a pursuit that never\n" +
			"comes up from one that comes up and is passed over.",
		Example: "  doit pursuits drift                                       the default window, up to now\n" +
			"  doit pursuits drift --since 2w                            the past two weeks\n" +
			"  doit pursuits drift --since 2026-10-01 --until 2026-10-31  one whole month\n" +
			"  doit pursuits drift --json                                the same rows as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, drift, err := client.Drift(cmd.Context(), since, until)
			if err != nil {
				return windowRefused(err)
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Drift(cmd.OutOrStdout(), drift, until == "", screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().StringVar(&since, "since", "", "Where the window starts: a span back from now (30m, 12h, 7d, 2w), a day or an RFC 3339 "+
		"instant. The service's default where neither end is given, and the first record with only --until")
	cmd.Flags().StringVar(&until, "until", "", "Where the window ends, in the same forms. A day includes all of itself. Now where omitted")
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newPursuitsDormantCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "dormant",
		Short: "Pursuits gone quiet for more than three of their own intervals",
		Long: "Pursuits gone quiet for more than three of their own intervals.\n\n" +
			"In --json, `days_since` is null for a pursuit never done. `doit pursuits list` shows every\n" +
			"pursuit, quiet or not.",
		Example: "  doit pursuits dormant         what has gone quiet, coldest first\n" +
			"  doit pursuits dormant --json  the same rows as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, dormant, err := client.Dormant(cmd.Context())
			if err != nil {
				return err
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Dormant(cmd.OutOrStdout(), dormant, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newPursuitsEvidenceCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "evidence",
		Short: "What each app says you last did, asked now rather than on the timer",
		Long: "What each app says you last did, and which of them could not be asked.\n\n" +
			"A pursuit whose app is logged out reads as never done, which looks the same as never\n" +
			"having done it. This is where the two are told apart.",
		Example: "  doit pursuits evidence         when each app says you last did it, and which apps could not answer\n" +
			"  doit pursuits evidence --json  the same answers as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			raw, evidence, err := client.Evidence(cmd.Context())
			if err != nil {
				return err
			}
			if asJSON {
				return printJSON(cmd.OutOrStdout(), raw)
			}
			return screens.Evidence(cmd.OutOrStdout(), evidence, screens.TerminalWidths(), gotermstyle.PaletteFor(os.Stdout))
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

func newPursuitsEditCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "edit",
		Short:   "Edit the pursuits in $EDITOR, starting from an annotated template where none are written",
		Example: "  doit pursuits edit  change a pace or a weight, or add a pursuit",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if err := editedOrExit(cmd, "pursuits"); err != nil {
				return err
			}
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			register, err := client.Register(cmd.Context())
			if err != nil {
				return err
			}
			saveCompletionNames(cmd.ErrOrStderr(), register.Pursuits)
			return nil
		},
	}
}

func newPursuitsNamesCommand() *cobra.Command {
	return &cobra.Command{
		Use:    "names",
		Short:  "Emit name<TAB>description for the shell completion",
		Hidden: true,
		Args:   cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, err := service.Open(cmd.Context())
			if err != nil {
				return err
			}
			register, err := client.Register(cmd.Context())
			if err != nil {
				return err
			}
			saveCompletionNames(cmd.ErrOrStderr(), register.Pursuits)
			names, err := os.ReadFile(deskfiles.CompletionNamesPath())
			if err != nil {
				return err
			}
			_, err = cmd.OutOrStdout().Write(names)
			return err
		},
	}
}
