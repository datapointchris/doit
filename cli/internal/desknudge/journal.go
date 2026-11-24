package desknudge

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"sync"
	"syscall"
)

// lineHandler writes one line per log record. Under systemd it leads each with
// the `<N>` priority that journald reads from a unit's stderr.
type lineHandler struct {
	out     io.Writer
	journal bool
	mu      *sync.Mutex
}

// StderrHandler logs to stderr, with journald's priorities where stderr is the
// journal stream that $JOURNAL_STREAM names.
func StderrHandler() slog.Handler {
	return lineHandler{out: os.Stderr, journal: toJournal(), mu: &sync.Mutex{}}
}

func toJournal() bool {
	var stat syscall.Stat_t
	if syscall.Fstat(int(os.Stderr.Fd()), &stat) != nil {
		return false
	}
	return os.Getenv("JOURNAL_STREAM") == fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)
}

func (h lineHandler) Enabled(_ context.Context, level slog.Level) bool {
	return level >= slog.LevelInfo
}

func (h lineHandler) Handle(_ context.Context, record slog.Record) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.journal {
		_, err := fmt.Fprintf(h.out, "<%d>%s\n", priority(record.Level), record.Message)
		return err
	}
	_, err := fmt.Fprintf(h.out, "desk-nudge %s: %s\n", record.Level, record.Message)
	return err
}

func (h lineHandler) WithAttrs([]slog.Attr) slog.Handler { return h }
func (h lineHandler) WithGroup(string) slog.Handler      { return h }

// priority is syslog's for a level: 3 error, 4 warning, 6 info.
func priority(level slog.Level) int {
	switch {
	case level >= slog.LevelError:
		return 3
	case level >= slog.LevelWarn:
		return 4
	}
	return 6
}
