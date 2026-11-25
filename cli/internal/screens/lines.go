// Package screens draws each of doit's screens from the service's answer.
//
// The helpers here print as rich does: a line cut at the terminal with an
// ellipsis, a sentence wrapped at its words, and a rule under a title. Each
// measures in terminal cells, as rich does, where gotermstyle.Clip counts
// runes. Text from the service is stripped of terminal controls before it is
// measured, as printedtable strips its cells, so stored text cannot drive the
// terminal. Layout is computed on the plain text, and color goes on after, so
// a cut can never land inside an escape sequence.
package screens

import (
	"io"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/charmbracelet/x/ansi"
	"github.com/datapointchris/gotermboard/terminalcontrol"
	"github.com/datapointchris/gotermstyle"
)

// Codes for dim text, a group heading and a late date on a dim row, beside gotermstyle's colors.
const (
	codeDim        = "\033[2m"
	codeGroup      = "\033[1;94m"
	codeDimYellow  = "\033[2;93m"
	ellipsis       = "…"
	maxLayoutWidth = 140
)

// Widths holds the console's width and the layout's width.
type Widths struct {
	// Console is where a printed line is cut or wrapped.
	Console int
	// Layout is what a multi-column view may spend: the terminal, capped so
	// a trailing column stays near the rest on an ultrawide one.
	Layout int
}

// TerminalWidths are this process's widths, both read from gotermstyle.Columns.
func TerminalWidths() Widths {
	columns := gotermstyle.Columns()
	return Widths{Console: columns, Layout: min(columns, maxLayoutWidth)}
}

// segment is a run of text in one color.
type segment struct {
	text string
	code string
}

// line is one printed line, as segments.
type line []segment

func (l line) plain() string {
	var b strings.Builder
	for _, s := range l {
		b.WriteString(s.text)
	}
	return b.String()
}

// add appends text in code, stripped of terminal controls.
func (l line) add(text, code string) line {
	return append(l, segment{terminalcontrol.Strip(text), code})
}

// cutTo keeps the first n bytes of the line's plain text.
func (l line) cutTo(n int) line {
	var kept line
	for _, s := range l {
		if n <= 0 {
			break
		}
		if len(s.text) > n {
			s.text = s.text[:n]
		}
		kept = append(kept, s)
		n -= len(s.text)
	}
	return kept
}

// rstripped is the line less its trailing whitespace.
func (l line) rstripped() line {
	plain := l.plain()
	return l.cutTo(len(strings.TrimRight(plain, " \t")))
}

// clipped is the line as rich prints it unwrapped: whitespace past width
// dropped, then cut to width with an ellipsis in its last cell.
func (l line) clipped(width int) line {
	plain := l.plain()
	if excess := cells(plain) - width; excess > 0 {
		trailing := len(plain) - len(strings.TrimRight(plain, " \t\n"))
		l = l.cutTo(len(plain) - min(trailing, excess))
		plain = l.plain()
	}
	if cells(plain) <= width {
		return l
	}
	kept := l.cutTo(len(ansi.Truncate(plain, max(width-1, 0), "")))
	code := ""
	if len(kept) > 0 {
		code = kept[len(kept)-1].code
	}
	return kept.add(ellipsis, code)
}

// wrapped is the line as rich prints it wrapped: broken before the word that
// would cross width, and a word wider than width folded across lines.
func (l line) wrapped(width int) []line {
	plain := l.plain()
	var lines []line
	from := 0
	for _, at := range append(breakOffsets(plain, width), len(plain)) {
		piece := l.slice(from, at)
		lines = append(lines, piece.croppedTo(width))
		from = at
	}
	return lines
}

// slice is the part of the line between two byte offsets of its plain text.
func (l line) slice(from, to int) line {
	var kept line
	at := 0
	for _, s := range l {
		start, end := at, at+len(s.text)
		at = end
		lo, hi := max(start, from), min(end, to)
		if lo < hi {
			kept = append(kept, segment{s.text[lo-start : hi-start], s.code})
		}
	}
	return kept
}

// croppedTo drops trailing whitespace past width, then cuts at width.
func (l line) croppedTo(width int) line {
	plain := l.plain()
	if excess := cells(plain) - width; excess > 0 {
		trailing := len(plain) - len(strings.TrimRight(plain, " \t\n"))
		l = l.cutTo(len(plain) - min(trailing, excess))
		plain = l.plain()
	}
	if cells(plain) <= width {
		return l
	}
	return l.cutTo(len(ansi.Truncate(plain, width, "")))
}

func (l line) paint(p gotermstyle.Palette) string {
	var b strings.Builder
	for _, s := range l {
		b.WriteString(p.Paint(s.code, s.text))
	}
	return b.String()
}

// words matches each word with the whitespace around it, as rich's divide_line reads a line.
var words = regexp.MustCompile(`\s*\S+\s*`)

// breakOffsets are the byte offsets rich's divide_line breaks plain at, folding a word wider than width.
func breakOffsets(plain string, width int) []int {
	var breaks []int
	offset := 0
	for _, match := range words.FindAllStringIndex(plain, -1) {
		start, word := match[0], plain[match[0]:match[1]]
		length := cells(strings.TrimRight(word, " \t\n"))
		switch {
		case width-offset >= length:
			offset += cells(word)
		case length > width:
			chunks := chopped(word, width)
			for index, chunk := range chunks {
				if start > 0 {
					breaks = append(breaks, start)
				}
				if index == len(chunks)-1 {
					offset = cells(chunk)
				} else {
					start += len(chunk)
				}
			}
		case offset > 0 && start > 0:
			breaks = append(breaks, start)
			offset = cells(word)
		}
	}
	return breaks
}

// chopped splits word into pieces of at most width cells.
func chopped(word string, width int) []string {
	var pieces []string
	var piece strings.Builder
	used := 0
	for _, r := range word {
		w := cells(string(r))
		if used+w > width && piece.Len() > 0 {
			pieces = append(pieces, piece.String())
			piece.Reset()
			used = 0
		}
		piece.WriteRune(r)
		used += w
	}
	return append(pieces, piece.String())
}

// cells is the printed width of plain text, once its controls are stripped.
func cells(plain string) int { return ansi.StringWidth(terminalcontrol.Strip(plain)) }

// runes is Python's len(): how many code points plain holds, once its controls are stripped.
func runes(plain string) int { return utf8.RuneCountInString(terminalcontrol.Strip(plain)) }

// fitted is text no wider than width, cut with an ellipsis, and padded to
// width where pad asks. A column granted no width draws nothing.
func fitted(text string, width int, pad bool) string {
	if width <= 0 {
		return ""
	}
	text = terminalcontrol.Strip(text)
	if cells(text) > width {
		text = ansi.Truncate(text, width-1, "") + ellipsis
	}
	if pad && cells(text) < width {
		text += strings.Repeat(" ", width-cells(text))
	}
	return text
}

// printer writes a screen's lines.
type printer struct {
	w       io.Writer
	p       gotermstyle.Palette
	widths  Widths
	written error
}

func (pr *printer) write(text string) {
	if pr.written == nil {
		_, pr.written = io.WriteString(pr.w, text+"\n")
	}
}

// cut prints l on one line, cut at the console's width.
func (pr *printer) cut(l line) { pr.write(l.clipped(pr.widths.Console).paint(pr.p)) }

// wrap prints l wrapped at the console's width.
func (pr *printer) wrap(l line) {
	for _, piece := range l.wrapped(pr.widths.Console) {
		pr.write(piece.paint(pr.p))
	}
}

// whole prints l as it is, however wide.
func (pr *printer) whole(l line) { pr.write(l.paint(pr.p)) }

func (pr *printer) blank() { pr.write("") }

// rule prints title, then a line ruled to the console's width.
func (pr *printer) rule(title line) {
	width := pr.widths.Console
	if width-2 <= 0 {
		pr.write(pr.p.Paint(gotermstyle.CodeGreen, strings.Repeat("─", max(width, 0))))
		return
	}
	title = title.clipped(width - 2)
	rest := width - cells(title.plain()) - 1
	pr.whole(title.add(" ", "").add(strings.Repeat("─", max(rest, 0)), gotermstyle.CodeGreen))
}
