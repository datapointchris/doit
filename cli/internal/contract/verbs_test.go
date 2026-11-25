//go:build unix

package contract

import (
	"encoding/json"
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
	on := newDesk(t, served.url, root)

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
		if exit != 0 || !strings.Contains(printed, " answered") {
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

	t.Run("the shell completion reads the names file next wrote", func(t *testing.T) {
		completion, stderr, exit := on.run(t, "shell", "completion", "zsh")
		read := regexp.MustCompile(`local cache="([^"]+)"`).FindStringSubmatch(completion)
		if exit != 0 || read == nil {
			t.Fatalf("doit shell completion zsh exited %d, naming no names file:\n%s%s", exit, completion, stderr)
		}
		names, err := os.ReadFile(read[1])
		if err != nil || !strings.Contains(string(names), "chores\tThe maintenance list\n") {
			t.Errorf("the completion reads %s, which holds %q (%v)", read[1], names, err)
		}
	})
}
