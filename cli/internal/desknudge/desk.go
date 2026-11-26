package desknudge

import (
	"context"
	"errors"
	"log/slog"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/goclilogin"

	"github.com/datapointchris/doit/cli/internal/actor"
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
		Machine: actor.MachineName(),
		Actor:   actor.Identity(),
		Version: strings.TrimPrefix(version, "v"),
	}
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
	if _, err := client.Get(ctx, "/desk-panel-view", query, &view); err != nil {
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
		_, err = client.Get(ctx, "/desk-nudge", nil, &answered)
	}
	return answered, err
}

// storedAnswer is the service's reply to an answer.
type storedAnswer struct {
	Interval
	Added *bool `json:"added,omitempty"`
	// Replaced are the nudge's other timing answers, which the service moved to its history for this one.
	Replaced []string `json:"replaced,omitempty"`
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

// interval reads as the status prints it, as Python's %g does.
func formatMinutes(minutes float64) string { return strconv.FormatFloat(minutes, 'g', -1, 64) }
