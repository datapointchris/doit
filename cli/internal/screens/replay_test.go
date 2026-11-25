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
// testdata/<answer>.json at that terminal width. A recording holds the layout
// rich printed for that answer, with two exceptions. printedtable measures the
// forecast tables where rich padded a fixed width. It cuts a drift cell that
// does not fit, where rich wrapped the cell onto more lines. The answers hold invented rows in
// the shape the service answers. A screen replays when it prints the same
// text, with its escapes, each line's trailing spaces and the blank lines that
// end it stripped, because the end-of-file hook strips those from a recording.
// A change meant to alter a screen rewrites them with
// `go test ./internal/screens -update`, which writes them stripped the same way.
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
			palette := gotermstyle.NewPalette(true)
			var out bytes.Buffer
			switch screen {
			case "today":
				var answer service.Today
				readAnswer(t, answerName, &answer)
				err = Today(&out, answer, widths, palette)
			case "next":
				var answer service.Next
				readAnswer(t, answerName, &answer)
				err = Next(&out, answer, drawnAt, widths, palette)
			case "explain":
				var answer service.Next
				readAnswer(t, answerName, &answer)
				err = Explain(&out, answer, widths, palette)
			case "happened", "happened-until":
				var answer service.Happened
				readAnswer(t, answerName, &answer)
				err = Happened(&out, answer, screen == "happened", widths, palette)
			case "dashboard":
				var answer service.Dashboard
				readAnswer(t, answerName, &answer)
				err = Dashboard(&out, answer, widths, palette)
			case "forecast":
				var answer service.Reading
				readAnswer(t, answerName, &answer)
				err = Forecast(&out, answer, widths, palette)
			case "forecasts":
				var answer []service.Reading
				readAnswer(t, answerName, &answer)
				err = Forecasts(&out, answer, widths, palette)
			case "trend":
				var answer service.Trend
				readAnswer(t, answerName, &answer)
				err = Trend(&out, answer, widths, palette)
			case "pursuits":
				var answer struct {
					List   service.PursuitList `json:"list"`
					Offers service.Offers      `json:"offers"`
				}
				readAnswer(t, answerName, &answer)
				err = PursuitList(&out, answer.List, answer.Offers, widths, palette)
			case "drift":
				var answer service.Drift
				readAnswer(t, answerName, &answer)
				err = Drift(&out, answer, true, widths, palette)
			case "dormant":
				var answer service.Dormant
				readAnswer(t, answerName, &answer)
				err = Dormant(&out, answer, widths, palette)
			case "evidence":
				var answer service.Evidence
				readAnswer(t, answerName, &answer)
				err = Evidence(&out, answer, widths, palette)
			case "review-list", "review-due":
				var answer service.Review
				readAnswer(t, answerName, &answer)
				if screen == "review-list" {
					err = ReviewList(&out, answer, widths, palette)
				} else {
					err = ReviewDue(&out, answer, widths, palette)
				}
			case "nudge":
				var answer service.Nudge
				readAnswer(t, answerName, &answer)
				err = ReviewNudge(&out, answer, widths, palette)
			case "sources":
				var answer service.Sources
				readAnswer(t, answerName, &answer)
				err = SourcesList(&out, answer, widths, palette)
			default:
				t.Fatalf("%s names no screen this package draws", path)
			}
			if err != nil {
				t.Fatal(err)
			}
			if *update {
				if err := os.WriteFile(path, []byte(plainText(out.String())+"\n"), 0o644); err != nil {
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

// plainText is printed text with its escapes, each line's trailing spaces and its closing blank lines stripped.
func plainText(printed string) string {
	lines := strings.Split(ansi.Strip(printed), "\n")
	for index, line := range lines {
		lines[index] = strings.TrimRight(line, " ")
	}
	return strings.TrimRight(strings.Join(lines, "\n"), "\n")
}
