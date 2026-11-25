package pursuits

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"golang.org/x/oauth2"

	"github.com/datapointchris/doit/cli/internal/service"
)

func TestALogWhoseAnswerIsLostAfterOnLogRanIsSentAgainUnderItsIDAndStoredOnce(t *testing.T) {
	var lock sync.Mutex
	stored, posts := map[string]int{}, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		lock.Lock()
		defer lock.Unlock()
		stored[body["id"].(string)]++
		posts++
		if posts == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"due_in_days": 1, "due": "due in 1d"}`))
	}))
	defer server.Close()
	client := service.New(server.URL, oauth2.StaticTokenSource(&oauth2.Token{AccessToken: "t"}), "", "")
	dir := t.TempDir()
	marker := filepath.Join(dir, "on_log-ran")
	template := "sh -c 'echo {id} >> " + marker + "'"
	held := HeldLogs{Dir: filepath.Join(dir, "held-logs")}
	run := Run{
		Check:  service.LogCheck{Pursuit: "tasks", OccurredAt: "2026-11-25T08:00:00-05:00", OnLog: &template, NamesChosen: true},
		Chosen: map[string]any{"id": "7", "label": "Sweep"},
		Entry:  Entry{ID: "0193f0a0-0000-7000-8000-000000000001"},
		Held:   held,
	}

	_, _, err := Log(context.Background(), client, run)
	var stillHeld *HeldError
	if !errors.As(err, &stillHeld) {
		t.Fatalf("a post answered 500 after on_log ran ended %v, not held", err)
	}
	sent, err := held.SendHeld(context.Background(), client)
	if err != nil || len(sent) != 1 || sent[0].Refused != nil {
		t.Fatalf("sending the held log again answered %+v, %v", sent, err)
	}

	ran, _ := os.ReadFile(marker)
	left, _ := os.ReadDir(held.Dir)
	if strings.Count(string(ran), "7\n") != 1 || len(stored) != 1 || len(left) != 0 {
		t.Fatalf("on_log ran %q, the service saw ids %v, and %d logs stay held", ran, stored, len(left))
	}
}
