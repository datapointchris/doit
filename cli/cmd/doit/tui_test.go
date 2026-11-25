package main

import (
	"encoding/json"
	"testing"

	"github.com/datapointchris/doit/cli/internal/desknudge"
)

// An agent runs doit tui with no terminal, so it is refused rather than left
// waiting on a keyboard, and --json hands it the now tab's snapshot.
func TestDoitTuiRefusesWithNoTerminalAndPrintsTheNowTabsSnapshotWithJSON(t *testing.T) {
	if refused := runDoitWith(t, nil, "tui"); refused.exit != 2 {
		t.Errorf("doit tui with no terminal: exit %d, stderr %q; want exit 2", refused.exit, refused.stderr)
	}

	printed := runDoitWith(t, nil, "tui", "--view", "now", "--json")
	var panel desknudge.Panel
	if err := json.Unmarshal([]byte(printed.stdout), &panel); err != nil || printed.exit != 0 {
		t.Fatalf("doit tui --json: exit %d, %v\n%s%s", printed.exit, err, printed.stdout, printed.stderr)
	}
	if panel.SchemaVersion != desknudge.PanelSchemaVersion || panel.Step != desknudge.StepToday || len(panel.Rows) == 0 {
		t.Errorf("doit tui --json printed %+v; want today's step with the note that no rows are read yet", panel)
	}
}
