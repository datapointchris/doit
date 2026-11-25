//go:build unix

package contract

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// reviewQueue answers GET /api/v1/review as ingestion's
// schemas/review-list.schema.json describes it. The second key holds the / and
// # that a path segment has to carry escaped, and its guess cleared the floor,
// so the lane shows why it waits instead.
const reviewQueue = `{"schema_version": 1, "items": [
  {"key": "reddit/t3_1h9xk2", "zone": "outside", "title": "r/productivity: Anyone read Deep Work by Cal Newport?",
   "url": "https://www.reddit.com/r/productivity/comments/1h9xk2/", "reason": "below-floor",
   "guess": {"side": "personal", "confidence": 0.58}, "connectors": ["reddit-exports"], "waiting_since_ts": "2026-11-20T21:44:00Z"},
  {"key": "memo/7c41e09a2b3d55f1#2", "zone": "owner", "title": "voice 18:02, topic 2 of 3", "url": null,
   "reason": "tags-disagree", "guess": {"side": "fleet", "confidence": 0.86}, "connectors": ["phone"],
   "waiting_since_ts": "2026-11-22T18:02:00Z"}
]}`

// decided is one placement or drop the ingestion stand-in was sent.
type decided struct {
	Verb   string
	Key    string
	Bearer string
	Body   map[string]any
}

// ingestionStandIn answers the review routes doit-api calls, and keeps every
// placement and drop it is sent. Each answer is shaped as ingestion's
// schemas/review-placement.schema.json describes it.
type ingestionStandIn struct {
	*httptest.Server
	mu   sync.Mutex
	sent []decided
}

func newIngestionStandIn(t *testing.T) *ingestionStandIn {
	t.Helper()
	standIn := &ingestionStandIn{}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/review", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(reviewQueue))
	})
	for _, verb := range []string{"place", "drop"} {
		mux.HandleFunc("POST /api/v1/review/{key}/"+verb, func(w http.ResponseWriter, r *http.Request) {
			var body map[string]any
			if json.NewDecoder(r.Body).Decode(&body) != nil {
				http.Error(w, `{"code": "invalid_body", "message": "not JSON"}`, http.StatusBadRequest)
				return
			}
			standIn.mu.Lock()
			standIn.sent = append(standIn.sent, decided{verb, r.PathValue("key"), r.Header.Get("Authorization"), body})
			standIn.mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(placementAnswer(r.PathValue("key"), body))
		})
	}
	standIn.Server = httptest.NewServer(mux)
	t.Cleanup(standIn.Close)
	return standIn
}

// placementAnswer is the side decision as person:<login>, then a read as is
// where the item took a side.
func placementAnswer(key string, body map[string]any) map[string]any {
	cause, _ := body["cause"].(string)
	if cause == "" {
		cause = "unstated"
	}
	login, _ := body["login"].(string)
	side := map[string]any{
		"gate": "side", "verdict": "drop", "decided_by": "person:" + login, "confidence": nil, "cause": cause,
		"destination": nil, "note": body["note"], "lens": nil, "client_id": "ingestion-svc-doit", "decided_ts": "2026-11-25T08:00:00Z",
	}
	decisions := []map[string]any{side}
	if destination, ok := body["destination"].(string); ok {
		side["verdict"], side["destination"] = "personal", destination
		decisions = append(decisions, map[string]any{
			"gate": "read", "verdict": "as-is", "decided_by": "rule:read-default", "confidence": nil, "cause": "rule",
			"destination": destination, "note": nil, "lens": nil, "client_id": nil, "decided_ts": "2026-11-25T08:00:00Z",
		})
	}
	return map[string]any{"schema_version": 1, "key": key, "decisions": decisions}
}

func (s *ingestionStandIn) decisions() []decided {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]decided(nil), s.sent...)
}

// The ingestion-review lane and the verbs that decide its items, against an
// ingestion that answers from its published review schemas.
func TestIngestionReviewLaneListsItemsAndPlacesThemThroughIngestion(t *testing.T) {
	if testing.Short() {
		t.Skip("starts the Python service, which the commit hook's -short run leaves to CI")
	}
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	ingestion := newIngestionStandIn(t)
	served := startService(t, root, "DOIT_API_INGESTION_URL="+ingestion.URL)
	served.writeText(t, "sources", "sources:\n  ingestion-review:\n    adapter: ingestion-review\n")
	on := newDesk(t, served.url, root)

	t.Run("dashboard lists each waiting item with its zone, title, guess below the floor and key", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "dashboard", "--lane", "ingestion-review")
		if exit != 0 {
			t.Fatalf("doit dashboard --lane ingestion-review exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		for _, wanted := range []string{
			"INGESTION-REVIEW", "2 waiting", "outside", "r/productivity: Anyone read Deep Work", "personal 0.58", "reddit/t3_1h9xk2",
			"owner", "tags-disagree", "memo/7c41e09a2b3d55f1#2", "doit ingestion items place <key> <destination>",
		} {
			if !strings.Contains(printed, wanted) {
				t.Errorf("the lane does not show %q:\n%s", wanted, printed)
			}
		}
	})

	t.Run("place and drop reach ingestion as the caller's decision, with its cause", func(t *testing.T) {
		printed, stderr, exit := on.run(t, "ingestion", "items", "place", "reddit/t3_1h9xk2", "life-articles", "--cause", "subreddit")
		if exit != 0 || !strings.Contains(printed, "Placed reddit/t3_1h9xk2 on life-articles: side personal, read as-is.") {
			t.Fatalf("doit ingestion items place exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		printed, stderr, exit = on.run(t, "ingestion", "items", "drop", "memo/7c41e09a2b3d55f1#2")
		if exit != 0 || !strings.Contains(printed, "Dropped memo/7c41e09a2b3d55f1#2, cause unstated.") {
			t.Fatalf("doit ingestion items drop exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		sent := ingestion.decisions()
		if len(sent) != 2 {
			t.Fatalf("ingestion was sent %d decisions, wanting the placement and the drop: %+v", len(sent), sent)
		}
		placed, dropped := sent[0], sent[1]
		if placed.Verb != "place" || placed.Key != "reddit/t3_1h9xk2" || placed.Bearer != "Bearer service-token" ||
			placed.Body["destination"] != "life-articles" || placed.Body["cause"] != "subreddit" || placed.Body["login"] != "chris" {
			t.Errorf("ingestion recorded the placement as %+v", placed)
		}
		if _, named := dropped.Body["cause"]; dropped.Verb != "drop" || dropped.Key != "memo/7c41e09a2b3d55f1#2" || named ||
			dropped.Body["login"] != "chris" {
			t.Errorf("ingestion recorded the drop as %+v", dropped)
		}
	})

	t.Run("an ingestion that cannot be reached shows the lane unavailable, never empty", func(t *testing.T) {
		ingestion.Close()
		printed, stderr, exit := on.run(t, "dashboard", "--lane", "ingestion-review", "--json")
		var answer struct {
			Lanes []struct {
				Name   string `json:"name"`
				Status string `json:"status"`
				Reason string `json:"reason"`
			} `json:"lanes"`
		}
		if exit != 0 || json.Unmarshal([]byte(printed), &answer) != nil || len(answer.Lanes) != 1 {
			t.Fatalf("doit dashboard --lane ingestion-review --json exited %d, printing:\n%s%s", exit, printed, stderr)
		}
		if lane := answer.Lanes[0]; lane.Status != "unavailable" || !strings.Contains(lane.Reason, "ingestion could not be reached") {
			t.Errorf("the lane read %+v", lane)
		}
	})
}
