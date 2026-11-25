//go:build unix

package contract

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// Each verb the Go doit runs, against the service it ships beside.
func TestEachPortedVerbReadsTheServiceFromThisCommit(t *testing.T) {
	if testing.Short() {
		t.Skip("starts the Python service, which the commit hook's -short run leaves to CI")
	}
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	served := startService(t, root)
	served.write(t, "pursuits", filepath.Join(root, "tests", "fixtures", "pursuits", "pursuits.yml"))
	served.write(t, "review", filepath.Join(root, "tests", "fixtures", "review", "register.yml"))
	on := newDesk(t, served.url)

	t.Run("today prints the day the service answers", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "today")
		if exit != 0 {
			t.Fatalf("doit today exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if !strings.Contains(printed, "never-done") {
			t.Errorf("the review item due since it was declared is not on the day:\n%s", printed)
		}
	})

	t.Run("today --json prints the route's answer and exits 0", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "today", "--json")
		var day struct {
			Due []struct {
				Name   string `json:"name"`
				RowCap *int   `json:"row_cap"`
			} `json:"due"`
			Done  []json.RawMessage `json:"done"`
			Quota json.RawMessage   `json:"quota"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &day) != nil || len(day.Due) == 0 || day.Quota == nil {
			t.Fatalf("doit today --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		for _, lane := range day.Due {
			if lane.RowCap == nil {
				t.Errorf("due group %s carries no row_cap", lane.Name)
			}
		}
	})

	var drawn struct {
		Offered []string `json:"offered"`
		State   struct {
			Active []string `json:"active"`
		} `json:"state"`
	}
	t.Run("next --json prints the draw", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "next", "--json")
		if exit != 0 || json.Unmarshal([]byte(printed), &drawn) != nil || len(drawn.Offered) == 0 {
			t.Fatalf("doit next --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
	})

	t.Run("next and next --explain give every active pursuit a row", func(t *testing.T) {
		for _, args := range [][]string{{"next"}, {"next", "--explain"}} {
			printed, stderr, exit := on.run(t, args...)
			if exit != 0 {
				t.Fatalf("doit %v exited %d, printing:\n%s%s", args, exit, printed, stderr)
			}
			for _, name := range drawn.State.Active {
				if !strings.Contains(printed, name) {
					t.Errorf("doit %v gives %s no row:\n%s", args, name, printed)
				}
			}
		}
	})

	t.Run("happened reads every record over the window and refuses one that will not parse", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "happened")
		if exit != 0 {
			t.Fatalf("doit happened exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		printed, stderr, exit = on.run(t, "happened", "--until", "2026-07-20", "--json")
		var window struct {
			Since string   `json:"since"`
			Until string   `json:"until"`
			Days  int      `json:"days"`
			Asked []string `json:"asked"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &window) != nil {
			t.Fatalf("doit happened --until 2026-07-20 --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if since, err := time.Parse(time.RFC3339, window.Since); err != nil || !since.Equal(time.Unix(0, 0)) ||
			!strings.HasPrefix(window.Until, "2026-07-21") || window.Days < 2 {
			t.Errorf("an end alone reads from the earliest record through that whole day, and the window was %+v", window)
		}
		if _, stderr, exit = on.run(t, "happened", "--since", "6mo"); exit != 2 {
			t.Errorf("a window with no month unit exited %d, wanting the usage exit 2: %s", exit, stderr)
		}
	})

	t.Run("dashboard draws the maintenance lane with no source and names a lane that matched none", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "dashboard")
		if exit != 0 || !strings.Contains(printed, "MAINTENANCE") || !strings.Contains(stderr, "No sources configured") {
			t.Fatalf("doit dashboard exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		printed, stderr, exit = on.run(t, "dashboard", "--lane", "nonsense", "--json")
		var board struct {
			Lanes []json.RawMessage `json:"lanes"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &board) != nil || len(board.Lanes) != 0 ||
			!strings.Contains(stderr, "No lane named nonsense.") {
			t.Errorf("doit dashboard --lane nonsense --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
	})

	t.Run("a forecast taken is read back and not graded before its window closes", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "forecast", "run", "--budget", "45")
		if exit != 0 || !strings.Contains(printed, "chores") {
			t.Fatalf("doit forecast run --budget 45 exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		printed, stderr, exit = on.run(t, "forecast", "show", "--json")
		var taken struct {
			BudgetMinutes int               `json:"budget_minutes"`
			Rows          []json.RawMessage `json:"rows"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &taken) != nil || taken.BudgetMinutes != 45 || len(taken.Rows) == 0 {
			t.Errorf("doit forecast show --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		for verb, wanted := range map[string]string{"show": "chores", "list": "1 reading ·"} {
			printed, stderr, exit := on.run(t, "forecast", verb)
			if exit != 0 || !strings.Contains(printed, wanted) {
				t.Errorf("doit forecast %s exited %d without %q, printing:\n%s%s", verb, exit, wanted, printed, stderr)
			}
		}
		if printed, stderr, exit := on.run(t, "forecast", "trend"); exit != 1 || printed != "" || !strings.Contains(stderr, "nothing can be graded") {
			t.Errorf("doit forecast trend exited %d before any window closed, printing:\n%s%s", exit, printed, stderr)
		}
	})

	t.Run("pursuits and pursuits list print each active pursuit", func(t *testing.T) {
		listed, stderr, exit := on.run(t, "pursuits", "list")
		if exit != 0 {
			t.Fatalf("doit pursuits list exited %d, printing:\n%s%s", exit, listed, stderr)
		}
		for _, name := range drawn.State.Active {
			if !strings.Contains(listed, name) {
				t.Errorf("doit pursuits list gives %s no row:\n%s", name, listed)
			}
		}
		if bare, _, _ := on.run(t, "pursuits"); bare != listed {
			t.Errorf("doit pursuits printed\n%s\nwhere doit pursuits list printed\n%s", bare, listed)
		}
		printed, stderr, exit := on.run(t, "pursuits", "list", "--json")
		var list struct {
			Pursuits map[string]json.RawMessage `json:"pursuits"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &list) != nil || list.Pursuits["chores"] == nil {
			t.Errorf("doit pursuits list --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
	})

	t.Run("pursuits log refuses what the service's check refuses, and writes nothing", func(t *testing.T) {
		for _, refused := range []struct {
			args []string
			exit int
			said string
		}{
			{[]string{"read", "--no-write"}, 2, "read is ambiguous: it matches read-library, read-longform"},
			{[]string{"nonsense"}, 2, "no pursuit named nonsense"},
			{[]string{"chores", "--minutes", "20", "--no-write"}, 2, ""},
			{[]string{"read-library", "--no-write"}, 2, ""},
			{[]string{"chores", "--ago", "3x", "--no-write"}, 2, ""},
			{[]string{"--no-write"}, 1, ""},
		} {
			printed, stderr, exit := on.run(t, append([]string{"pursuits", "log"}, refused.args...)...)
			if exit != refused.exit || !strings.Contains(stderr, refused.said) {
				t.Errorf("doit pursuits log %v exited %d without %q, printing:\n%s%s", refused.args, exit, refused.said, printed, stderr)
			}
		}
		if written := served.journal(t, ""); len(written) != 0 {
			t.Errorf("a refused log wrote %v", written)
		}
	})

	t.Run("pursuits log writes one journal event, which drift and dormant then read", func(t *testing.T) {
		if printed, stderr, exit := on.run(t, "pursuits", "log", "chores", "swept", "--no-write"); exit != 0 {
			t.Fatalf("doit pursuits log chores exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if printed, stderr, exit := on.run(t, "pursuits", "log", "read-library", "--minutes", "20", "--no-write"); exit != 0 {
			t.Fatalf("doit pursuits log read-library --minutes 20 exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		chores, reading := served.journal(t, "chores"), served.journal(t, "read-library")
		if len(chores) != 1 || chores[0].Note == nil || *chores[0].Note != "swept" {
			t.Errorf("one log of chores wrote %+v", chores)
		}
		if len(reading) != 1 || reading[0].Minutes == nil || *reading[0].Minutes != 20 {
			t.Errorf("one log of read-library for 20 minutes wrote %+v", reading)
		}
		for _, args := range [][]string{{"drift"}, {"dormant"}, {"evidence"}} {
			if printed, stderr, exit := on.run(t, append([]string{"pursuits"}, args...)...); exit != 0 || printed == "" {
				t.Errorf("doit pursuits %v exited %d, printing:\n%s%s", args, exit, printed, stderr)
			}
		}
		printed, stderr, exit := on.run(t, "pursuits", "dormant", "--json")
		var dormant struct {
			SchemaVersion int `json:"schema_version"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &dormant) != nil || dormant.SchemaVersion != 2 {
			t.Errorf("doit pursuits dormant --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if _, stderr, exit := on.run(t, "pursuits", "drift", "--since", "6mo"); exit != 2 {
			t.Errorf("a drift window with no month unit exited %d, wanting the usage exit 2: %s", exit, stderr)
		}
	})

	t.Run("pursuits skip, resume and reset each write their event, and a reset of all is confirmed", func(t *testing.T) {
		if printed, stderr, exit := on.run(t, "pursuits", "skip", "read-library", "--for", "3d"); exit != 0 {
			t.Errorf("doit pursuits skip read-library --for 3d exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		skips := served.events(t, "read-library", "skip")
		if len(skips) != 1 || skips[0].ExpiresAt == nil || time.Until(*skips[0].ExpiresAt).Round(time.Hour) != 72*time.Hour {
			t.Errorf("a skip for 3d wrote %+v", skips)
		}
		if _, stderr, exit := on.run(t, "pursuits", "skip", "chores", "--for", "3m"); exit != 2 {
			t.Errorf("a skip for a span of minutes exited %d, wanting the usage exit 2: %s", exit, stderr)
		}
		reset := len(served.events(t, "chores", "reset"))
		for _, args := range [][]string{{"resume", "read-library"}, {"reset", "chores"}} {
			if printed, stderr, exit := on.run(t, append([]string{"pursuits"}, args...)...); exit != 0 {
				t.Errorf("doit pursuits %v exited %d, printing:\n%s%s", args, exit, printed, stderr)
			}
		}
		if skips := served.events(t, "read-library", "skip"); len(skips) != 2 || skips[1].ExpiresAt == nil || time.Until(*skips[1].ExpiresAt) > time.Minute {
			t.Errorf("a resume left the skips %+v, the newest not ending now", skips)
		}
		resets := len(served.events(t, "", "reset"))
		if _, stderr, exit := on.run(t, "pursuits", "reset"); exit != 1 {
			t.Errorf("a reset of every pursuit with nobody to confirm exited %d: %s", exit, stderr)
		}
		if after := len(served.events(t, "chores", "reset")); after != reset+1 {
			t.Errorf("a reset of chores wrote %d reset events of it", after-reset)
		}
		if after := len(served.events(t, "", "reset")); after != resets {
			t.Errorf("a refused reset of every pursuit wrote %d reset events", after-resets)
		}
	})

	t.Run("review done records one completion and undo takes the newest back", func(t *testing.T) {
		if printed, stderr, exit := on.run(t, "review", "due"); exit != 0 || !strings.Contains(printed, "never-done") {
			t.Errorf("doit review due exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		today := time.Now().Format(time.DateOnly)
		for _, args := range [][]string{{"done", "never-done", "--on", "2026-07-17"}, {"done", "never-done"}} {
			if printed, stderr, exit := on.run(t, append([]string{"review"}, args...)...); exit != 0 {
				t.Fatalf("doit review %v exited %d, printing:\n%s%s", args, exit, printed, stderr)
			}
		}
		if days := served.completions(t, "never-done"); strings.Join(days, " ") != "2026-07-17 "+today {
			t.Errorf("two completions recorded %v", days)
		}
		tomorrow := time.Now().AddDate(0, 0, 1).Format(time.DateOnly)
		if _, stderr, exit := on.run(t, "review", "done", "never-done", "--on", tomorrow); exit != 2 {
			t.Errorf("a completion on a day not yet come exited %d, wanting the usage exit 2: %s", exit, stderr)
		}
		if printed, stderr, exit := on.run(t, "review", "undo", "never-done"); exit != 0 || !strings.Contains(printed, "Took back never-done ("+today+")") {
			t.Errorf("doit review undo exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if printed, _, _ := on.run(t, "review", "undo", "never-done"); !strings.Contains(printed, "Now: never done.") {
			t.Errorf("taking back the last completion printed:\n%s", printed)
		}
		if _, stderr, exit := on.run(t, "review", "undo", "never-done"); exit != 1 {
			t.Errorf("an undo with nothing to take back exited %d: %s", exit, stderr)
		}
		for _, verb := range []string{"done", "undo"} {
			if _, stderr, exit := on.run(t, "review", verb, "no-such-entry"); exit != 2 {
				t.Errorf("doit review %s of an id the register lacks exited %d, wanting the usage exit 2: %s", verb, exit, stderr)
			}
		}
		printed, stderr, exit := on.run(t, "review", "list", "--json")
		var entries []struct {
			ID  string `json:"id"`
			Due bool   `json:"due"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &entries) != nil || len(entries) == 0 {
			t.Errorf("doit review list --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
	})

	t.Run("review nudge shows what is due once per interval and runs each show", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "review", "nudge")
		for _, wanted := range []string{"never-done", "echo overdue", "fresh\n"} {
			if exit != 0 || !strings.Contains(printed, wanted) {
				t.Errorf("doit review nudge exited %d without %q, printing:\n%s%s", exit, wanted, printed, stderr)
			}
		}
		if again, stderr, exit := on.run(t, "review", "nudge"); exit != 0 || again != "" {
			t.Errorf("a second nudge inside the interval exited %d, printing:\n%s%s", exit, again, stderr)
		}
		if printed, stderr, exit := on.run(t, "review", "nudge-every", "90m"); exit != 0 {
			t.Errorf("doit review nudge-every 90m exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		gate, stderr, exit := on.run(t, "shell", "init", "zsh")
		read := regexp.MustCompile(`_doit_mins="\$\(cat "([^"]+)"`).FindStringSubmatch(gate)
		if exit != 0 || read == nil {
			t.Fatalf("doit shell init zsh exited %d, naming no interval file:\n%s%s", exit, gate, stderr)
		}
		if held, err := os.ReadFile(read[1]); err != nil || string(held) != "90\n" {
			t.Errorf("the shell gate reads %s, which holds %q (%v)", read[1], held, err)
		}
		if _, stderr, exit := on.run(t, "review", "nudge-every", "soon"); exit != 2 {
			t.Errorf("an interval that is no duration exited %d, wanting the usage exit 2: %s", exit, stderr)
		}
	})

	t.Run("sources list, example and contract answer with no source configured", func(t *testing.T) {
		for wanted, args := range map[string][]string{
			"[]\n":     {"list", "--json"},
			"sources:": {"example"},
			`"lanes"`:  {"contract"},
		} {
			if printed, stderr, exit := on.run(t, append([]string{"sources"}, args...)...); exit != 0 || !strings.Contains(printed, wanted) {
				t.Errorf("doit sources %v exited %d without %q, printing:\n%s%s", args, exit, wanted, printed, stderr)
			}
		}
	})

	t.Run("a Tab after pursuits log offers the names next wrote", func(t *testing.T) {
		offered, stderr, exit := on.run(t, "__complete", "pursuits", "log", "")
		if exit != 0 || !strings.Contains(offered, "chores\tThe maintenance list\n") {
			t.Errorf("doit __complete pursuits log '' exited %d without the chores pursuit, printing:\n%s%s", exit, offered, stderr)
		}
	})

	// Last, because it declares a register of one pursuit.
	t.Run("pursuits log runs on_log on the item named, after the check, and the log records what it did", func(t *testing.T) {
		marker := filepath.Join(t.TempDir(), "completed")
		served.writeText(t, "pursuits", "pursuits:\n  tasks:\n    weight: 25\n    cadence: 3d\n"+
			"    resolve: {app: life, path: /tasks}\n    label: name\n    id: id\n"+
			"    on_log: sh -c 'printf %s \"$1\" > "+marker+"' _ {label}\n")
		if printed, stderr, exit := on.run(t, "pursuits", "log", "tasks", "--item", "999", "--yes"); exit != 2 || !strings.Contains(stderr, "512") {
			t.Errorf("an item no choice carries exited %d, naming no id it holds:\n%s%s", exit, printed, stderr)
		}
		if _, err := os.Stat(marker); err == nil || len(served.journal(t, "tasks")) != 0 {
			t.Fatal("a refused item ran on_log or wrote a log")
		}
		printed, stderr, exit := on.run(t, "pursuits", "log", "tasks", "--item", "512", "--yes")
		if exit != 0 {
			t.Fatalf("doit pursuits log tasks --item 512 --yes exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if completed, err := os.ReadFile(marker); err != nil || string(completed) != "Return the parcel" {
			t.Errorf("on_log was handed %q, where the label split as one argument is Return the parcel (%v)", completed, err)
		}
		logged := served.journal(t, "tasks")
		if len(logged) != 1 || logged[0].Item == nil || logged[0].Item.Label != "Return the parcel" ||
			logged[0].Downstream == nil || !logged[0].Downstream.Ran || logged[0].Downstream.ExitCode == nil || *logged[0].Downstream.ExitCode != 0 {
			t.Errorf("the log of task 512 wrote %+v", logged)
		}
		if err := os.Remove(marker); err != nil {
			t.Fatal(err)
		}
		if printed, stderr, exit := on.run(t, "pursuits", "log", "tasks", "--yes"); exit != 0 {
			t.Errorf("three choices with nobody to ask which exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if _, err := os.Stat(marker); err == nil {
			t.Error("a log among three choices that names none ran on_log")
		}
		if logged := served.journal(t, "tasks"); len(logged) != 2 || logged[1].Item != nil || logged[1].Downstream != nil {
			t.Errorf("a log among three choices that names none wrote %+v", logged)
		}
	})
}

// logged is one done event of the journal.
type logged struct {
	Event     string     `json:"event"`
	ExpiresAt *time.Time `json:"expires_at"`
	Note      *string    `json:"note"`
	Minutes   *int       `json:"duration_minutes"`
	Item      *struct {
		Label string `json:"label"`
	} `json:"item"`
	Downstream *struct {
		Ran      bool `json:"ran"`
		ExitCode *int `json:"exit_code"`
	} `json:"downstream"`
}

// journal is every done event of pursuit, or of every pursuit where it is "".
func (s service) journal(t *testing.T, pursuit string) []logged {
	t.Helper()
	return s.events(t, pursuit, "done")
}

// events is every event of pursuit of the kind named, or of every pursuit where it is "".
func (s service) events(t *testing.T, pursuit, event string) []logged {
	t.Helper()
	path := "/api/v1/journal"
	if pursuit != "" {
		path += "?pursuit=" + url.QueryEscape(pursuit)
	}
	var records []logged
	s.read(t, path, &records)
	var kept []logged
	for _, record := range records {
		if record.Event == event {
			kept = append(kept, record)
		}
	}
	return kept
}

// completions is each day entry holds a completion on, oldest first.
func (s service) completions(t *testing.T, entry string) []string {
	t.Helper()
	var held []struct {
		DoneOn string `json:"done_on"`
	}
	s.read(t, "/api/v1/review/"+url.PathEscape(entry)+"/completions", &held)
	days := make([]string, 0, len(held))
	for _, completion := range held {
		days = append(days, completion.DoneOn)
	}
	return days
}
