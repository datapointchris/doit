package main

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/datapointchris/doit/cli/internal/desknudge"
)

// An agent runs doit tui with no terminal, so it is refused rather than left
// waiting on a keyboard, and --json hands it the panel document the tab draws.
func TestDoitTuiRefusesWithNoTerminalAndPrintsThePanelDocumentWithJSON(t *testing.T) {
	refused := runDoitWith(t, nil, "tui")
	if refused.exit != 2 || !strings.Contains(refused.stderr, "--once") || !strings.Contains(refused.stderr, "--json") {
		t.Errorf("doit tui with no terminal: exit %d, stderr %q; want exit 2 naming --once and --json", refused.exit, refused.stderr)
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
