package desknudge

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/goclilogin"

	"github.com/datapointchris/doit/cli/internal/login"
	"github.com/datapointchris/doit/cli/internal/service"
)

// The bounds on each request the desk nudge makes, past which it gives up and
// says why. The view reads every app the service asks, as /today does.
const (
	viewBound     = 60 * time.Second
	intervalBound = 10 * time.Second
	answerBound   = 10 * time.Second
	writeBound    = 30 * time.Second
)

// logger is where the desk nudge logs. The watch command points it at stderr.
var logger = slog.New(slog.DiscardHandler)

// SetLogger points the desk nudge's log at handler.
func SetLogger(handler slog.Handler) { logger = slog.New(handler) }

// Desk is this desk: its files, its clock, the service it asks, and who it is.
type Desk struct {
	Paths Paths
	Now   func() time.Time
	// Open is the service this desk's login names, opened afresh for each
	// request so a new login is taken at once.
	Open    func(context.Context) (*service.Client, error)
	Machine string
	Actor   string
	// Version is the doit release this binary is.
	Version string
}

// ThisDesk is the desk this process runs on.
func ThisDesk(version string) *Desk {
	return &Desk{
		Paths:   DeskPaths(),
		Now:     time.Now,
		Open:    service.Open,
		Machine: machineName(),
		Actor:   actorIdentity(),
		Version: strings.TrimPrefix(version, "v"),
	}
}

// machineName is this machine's identity: its hostname's first label, lowercased.
func machineName() string {
	host, _ := os.Hostname()
	first, _, _ := strings.Cut(host, ".")
	return strings.ToLower(strings.TrimSpace(first))
}

// actorIdentity is who a journal write names: a Claude session, a scheduled
// job, or the user at this host, spelled as the Python doit spells it.
func actorIdentity() string {
	job, session := os.Getenv("SLATE_ACTOR"), os.Getenv("CLAUDE_CODE_SESSION_ID")
	switch {
	case session != "" && job != "":
		return "claude-code-unattended/" + job + "/" + session
	case session != "" && os.Getenv("CLAUDE_CODE_SESSION_ATTENDED") == "0":
		return "claude-code-unattended/" + session
	case session != "":
		return "claude-code/" + session
	case job != "":
		return "worker/" + job
	}
	user := os.Getenv("USER")
	if user == "" {
		user = "unknown"
	}
	if host, _ := os.Hostname(); host != "" {
		return user + "@" + host
	}
	return user
}

// newID is a UUIDv7: a 48-bit millisecond timestamp, then randomness, so ids
// sort into the order things happened.
func newID(now time.Time) string {
	var id [16]byte
	var millis [8]byte
	binary.BigEndian.PutUint64(millis[:], uint64(now.UnixMilli()))
	copy(id[0:6], millis[2:8])
	_, _ = rand.Read(id[6:])
	id[6] = 0x70 | id[6]&0x0f
	id[8] = 0x80 | id[8]&0x3f
	text := hex.EncodeToString(id[:])
	return text[0:8] + "-" + text[8:12] + "-" + text[12:16] + "-" + text[16:20] + "-" + text[20:]
}

// failureKind is why a read of the service failed, in the words a suggestion
// and the panel's banner use.
func failureKind(err error) string {
	var none *service.NoAnswer
	var missing *service.MissingField
	var version *ViewVersionError
	var refused *service.Refused
	switch {
	case errors.As(err, &none):
		return Unreachable
	case errors.As(err, &missing), errors.As(err, &version):
		return Unreadable
	case errors.Is(err, login.ErrNeedsLogin), errors.Is(err, goclilogin.ErrNotConfigured):
		return LoginRefused
	case errors.As(err, &refused) && (refused.Status == 0 || refused.Status == 401):
		return LoginRefused
	}
	return Unreachable
}

// readView reads the desk panel view. Only with draw does the service draw,
// save the draw and count an offer to this desk. A view at another version is
// refused, so the panel keeps the last view it reads rather than misreading one.
func (d *Desk) readView(ctx context.Context, draw bool) (*View, error) {
	ctx, cancel := context.WithTimeout(ctx, viewBound)
	defer cancel()
	client, err := d.Open(ctx)
	if err != nil {
		return nil, err
	}
	query := url.Values{"machine": {d.Machine}}
	if draw {
		query.Set("draw", "true")
	}
	var view View
	if err := client.Get(ctx, "/desk-panel-view", query, &view); err != nil {
		return nil, err
	}
	if view.SchemaVersion != viewSchemaVersion {
		return nil, &ViewVersionError{Version: view.SchemaVersion}
	}
	return &view, nil
}

// readInterval is the interval and the answer counts as the service computes them now.
func (d *Desk) readInterval(ctx context.Context) (Interval, error) {
	ctx, cancel := context.WithTimeout(ctx, intervalBound)
	defer cancel()
	var answered Interval
	client, err := d.Open(ctx)
	if err == nil {
		err = client.Get(ctx, "/desk-nudge", nil, &answered)
	}
	return answered, err
}

// storedAnswer is the service's reply to an answer.
type storedAnswer struct {
	Interval
	Added *bool `json:"added,omitempty"`
}

// sendAnswer stores one answer. It is repeatable, because the service stores a
// (nudge_id, answer) once and answers the first row again.
func (d *Desk) sendAnswer(ctx context.Context, row AnswerRow) (storedAnswer, error) {
	ctx, cancel := context.WithTimeout(ctx, answerBound)
	defer cancel()
	var answered storedAnswer
	client, err := d.Open(ctx)
	if err == nil {
		err = client.Post(ctx, "/desk-nudge/answers", row, true, &answered)
	}
	return answered, err
}

// post writes body to the route at path, for a pick's completion.
func (d *Desk) post(ctx context.Context, path string, body, answer any) error {
	ctx, cancel := context.WithTimeout(ctx, writeBound)
	defer cancel()
	client, err := d.Open(ctx)
	if err != nil {
		return err
	}
	return client.Post(ctx, path, body, true, answer)
}

// get reads the route at path, for a pick's completion.
func (d *Desk) get(ctx context.Context, path string, answer any) error {
	ctx, cancel := context.WithTimeout(ctx, writeBound)
	defer cancel()
	client, err := d.Open(ctx)
	if err != nil {
		return err
	}
	return client.Get(ctx, path, nil, answer)
}

// interval reads as the status prints it, as Python's %g does.
func formatMinutes(minutes float64) string { return strconv.FormatFloat(minutes, 'g', -1, 64) }
