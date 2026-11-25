package desknudge

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// schemaCompiler holds every file in the repository's schemas/, so a $ref by $id resolves.
func schemaCompiler(t *testing.T) *jsonschema.Compiler {
	t.Helper()
	files, err := filepath.Glob(filepath.Join("..", "..", "..", "schemas", "*.schema.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no schema files beside the repository: %v", err)
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	for _, file := range files {
		text, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		document, err := jsonschema.UnmarshalJSON(bytes.NewReader(text))
		if err != nil {
			t.Fatalf("%s: %v", file, err)
		}
		id, _ := document.(map[string]any)["$id"].(string)
		if err := compiler.AddResource(id, document); err != nil {
			t.Fatalf("%s: %v", file, err)
		}
	}
	return compiler
}

func matches(t *testing.T, compiler *jsonschema.Compiler, id, what string, document any) {
	t.Helper()
	schema, err := compiler.Compile(id)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := jsonschema.UnmarshalJSON(bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	if err := schema.Validate(parsed); err != nil {
		t.Errorf("%s does not match %s: %v", what, id, err)
	}
}

func TestEachDocumentTheWatchWritesMatchesItsSchemaFile(t *testing.T) {
	compiler := schemaCompiler(t)
	f := newFakeDesk(t)
	var view any
	_ = json.Unmarshal(f.view, &view)
	matches(t, compiler, "urn:doit:desk-panel-view:v1", "the fake service's view", view)

	const panelSchema = "urn:doit:desk-nudge-panel:v3"
	matches(t, compiler, panelSchema, "panel show before the first read", f.desk.Show())

	watch := f.started()
	f.readyNudge(watch)
	var cached any
	_ = json.Unmarshal(must(os.ReadFile(f.desk.Paths.Panel)), &cached)
	matches(t, compiler, panelSchema, "the panel the watch keeps", cached)
	matches(t, compiler, panelSchema, "panel show under a ready nudge", f.desk.Show())
	matches(t, compiler, "urn:doit:desk-nudge-status:v4", "status under a ready nudge", f.desk.Status())

	ctx := context.Background()
	minutes, err := watch.Pick(ctx, "pursuit:read", nil, true)
	if err != nil || minutes.Step != StepMinutes || minutes.Minutes == nil ||
		!slices.ContainsFunc(minutes.Rows, func(row Row) bool {
			return row.Key != nil && *row.Key == "log_minutes:45:pursuit:read" && row.Suggested
		}) {
		t.Fatalf("picking a timed pursuit answered %+v, %v; want its minutes step offering its usual 45 first", minutes.Rows, err)
	}
	matches(t, compiler, panelSchema, "a timed pursuit's minutes step", minutes)
	for _, key := range []string{"log_minutes:45:pursuit:read", "pursuit:tasks:532", "habit:1"} {
		done, err := watch.Pick(ctx, key, nil, true)
		if err != nil || done.Step != StepDone {
			t.Fatalf("picking %s answered %+v, %v; want a done step", key, done, err)
		}
		matches(t, compiler, panelSchema, "the done step of "+key, done)
	}
	if _, logs := f.posted(); len(logs) != 2 || logs[0]["duration_minutes"] != 45.0 || logs[1]["item"].(map[string]any)["label"] != `File the "receipts" now` {
		t.Errorf("the journal took %v, want read logged at 45 minutes, then the task naming its item", logs)
	}

	f.lifeHolds("tick")
	go func() { _, _ = watch.Pick(ctx, "tick:1", nil, false) }()
	f.waitFor("the tick running", func() bool { return f.running("tick:1") })
	matches(t, compiler, panelSchema, "panel show with a pick running", f.desk.Show())
	f.releaseLife()
	watch.Wait()
	matches(t, compiler, panelSchema, "panel show with a tick done", f.desk.Show())

	if _, err := f.desk.Pause(); err != nil {
		t.Fatal(err)
	}
	matches(t, compiler, "urn:doit:desk-nudge-status:v4", "status while paused", f.desk.Status())
	if _, err := f.desk.Resume(); err != nil {
		t.Fatal(err)
	}
	f.down.Store(true)
	if _, err := f.desk.Answer(ctx, "too_soon", f.state().RecentNudges[0].NudgeID); err != nil {
		t.Fatal(err)
	}

	lines := strings.Split(strings.TrimSpace(string(must(os.ReadFile(f.desk.Paths.Records)))), "\n")
	var events []string
	for _, line := range lines {
		var record any
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("an events line is not JSON: %q", line)
		}
		events = append(events, record.(map[string]any)["event"].(string))
		matches(t, compiler, "urn:doit:desk-nudge-record:v5", "the events line "+line, record)
	}
	for _, event := range []string{eventStarted, eventFired, eventPicked, eventPaused, eventResumed, eventAnswered} {
		if !slices.Contains(events, event) {
			t.Errorf("the events file holds %v, want a %s line among them", events, event)
		}
	}
}

func must(data []byte, err error) []byte {
	if err != nil {
		panic(err)
	}
	return data
}
