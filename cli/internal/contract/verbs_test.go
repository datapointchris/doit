//go:build unix

package contract

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
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
