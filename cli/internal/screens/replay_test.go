package screens

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/charmbracelet/x/ansi"
	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

var update = flag.Bool("update", false, "rewrite each recorded screen in testdata from what this package prints")

// Each testdata/<answer>.<screen>.<width>.txt is what the screen prints for
// testdata/<answer>.json at that terminal width. Each recording is the text
// rich printed for that answer, so a replay holds the Go screen to rich's
// layout. The answers hold invented rows in the shape the service answers. A
// screen replays when it prints the same text, with its escapes and each
// line's trailing spaces stripped. A change meant to alter a screen rewrites
// them with `go test ./internal/screens -update`.
func TestEachScreenPrintsWhatItsRecordingHolds(t *testing.T) {
	printed, err := filepath.Glob("testdata/*.*.*.txt")
	if err != nil || len(printed) == 0 {
		t.Fatalf("no recorded screens in testdata: %v", err)
	}
	drawnAt := time.Date(2026, 11, 2, 12, 0, 0, 0, time.FixedZone("", -5*3600))
	for _, path := range printed {
		parts := strings.Split(strings.TrimSuffix(filepath.Base(path), ".txt"), ".")
		answerName, screen := parts[0], parts[1]
		width, err := strconv.Atoi(parts[2])
		if err != nil {
			t.Fatalf("%s names no width", path)
		}
		t.Run(filepath.Base(path), func(t *testing.T) {
			widths := Widths{Console: width, Layout: min(width, maxLayoutWidth)}
			var out bytes.Buffer
			switch screen {
			case "today":
				var answer service.Today
				readAnswer(t, answerName, &answer)
				err = Today(&out, answer, widths, gotermstyle.NewPalette(true))
			case "next":
				var answer service.Next
				readAnswer(t, answerName, &answer)
				err = Next(&out, answer, drawnAt, widths, gotermstyle.NewPalette(true))
			case "explain":
				var answer service.Next
				readAnswer(t, answerName, &answer)
				err = Explain(&out, answer, widths, gotermstyle.NewPalette(true))
			default:
				t.Fatalf("%s names no screen this package draws", path)
			}
			if err != nil {
				t.Fatal(err)
			}
			if *update {
				if err := os.WriteFile(path, out.Bytes(), 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			want, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if got, expected := plainText(out.String()), plainText(string(want)); got != expected {
				t.Errorf("printed:\n%s\nrecorded:\n%s", got, expected)
			}
		})
	}
}

func readAnswer(t *testing.T, name string, answer any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name+".json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, answer); err != nil {
		t.Fatal(err)
	}
}

// plainText is printed text with its escapes and each line's trailing spaces stripped.
func plainText(printed string) string {
	lines := strings.Split(ansi.Strip(printed), "\n")
	for index, line := range lines {
		lines[index] = strings.TrimRight(line, " ")
	}
	return strings.Join(lines, "\n")
}
