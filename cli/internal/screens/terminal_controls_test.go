package screens

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/datapointchris/gotermstyle"

	"github.com/datapointchris/doit/cli/internal/service"
)

// A review item's text, a pursuit's choice and its reason are stored text. An
// OSC 52 in any of them would set the clipboard of whoever runs doit today,
// doit next or doit next --explain.
func TestStoredTextWithAnOSC52ReachesNoScreenAsATerminalControl(t *testing.T) {
	const osc52 = "\x1b]52;c;aGk=\x07"
	var day service.Today
	readAnswer(t, "today-full", &day)
	day.Due[0].Rows[0].Text += osc52
	var draw service.Next
	readAnswer(t, "next-with-events", &draw)
	draw.OfferRows[0].Choices = append(draw.OfferRows[0].Choices, "Fold the laundry"+osc52)
	draw.ExplainRows[0].Reason = "It keeps the house standing" + osc52

	widths := Widths{Console: 100, Layout: 100}
	plain := gotermstyle.NewPalette(false)
	var out bytes.Buffer
	if err := Today(&out, day, widths, plain); err != nil {
		t.Fatal(err)
	}
	if err := Next(&out, draw, time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC), widths, plain); err != nil {
		t.Fatal(err)
	}
	if err := Explain(&out, draw, widths, plain); err != nil {
		t.Fatal(err)
	}
	if strings.ContainsRune(out.String(), '\x1b') || strings.ContainsRune(out.String(), '\a') {
		t.Errorf("a screen printed a terminal control from stored text:\n%q", out.String())
	}
	for _, kept := range []string{"Fold the laundry", "It keeps the house standing"} {
		if !strings.Contains(out.String(), kept) {
			t.Errorf("the text around the control is gone: %q is not printed", kept)
		}
	}
}
