package pursuits

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/datapointchris/doit/cli/internal/deskfiles"
	"github.com/datapointchris/doit/cli/internal/service"
)

// HeldLogs is a directory of logs sent from this desk and not yet seen
// stored, one file per entry, named by its id. A log is held from just
// before its post until the service answers it. When an answer is lost after
// on_log acted, the entry is sent again under its own id, so the service
// stores it once and on_log never runs twice.
type HeldLogs struct {
	Dir string
}

// DeskHeldLogs are this desk's, under $XDG_STATE_HOME.
func DeskHeldLogs() HeldLogs { return HeldLogs{Dir: filepath.Join(deskfiles.StateDir(), "held-logs")} }

// HeldError is a log that got no answer saying it was stored. Its entry is
// held at Path, and the next log of any pursuit on this desk sends it again.
type HeldError struct {
	Err  error
	Path string
}

func (e *HeldError) Error() string { return e.Err.Error() }

func (e *HeldError) Unwrap() error { return e.Err }

// path is where the entry keyed id is held.
func (h HeldLogs) path(id string) string { return filepath.Join(h.Dir, id+".json") }

// Post holds body while it posts it. An entry the service stores, or refuses,
// is let go, because a later send changes neither. Any other failure keeps
// it, and answers a *HeldError.
func (h HeldLogs) Post(ctx context.Context, client *service.Client, body map[string]any) (service.Logged, error) {
	path, holdErr := h.Hold(body)
	logged, err := client.Log(ctx, body)
	switch {
	case err == nil, !service.LaterSendCanStore(err):
		_ = os.Remove(path)
	case holdErr == nil:
		return logged, &HeldError{Err: err, Path: path}
	}
	return logged, err
}

// Hold keeps body on this desk until a send of it is answered, and answers where.
func (h HeldLogs) Hold(body map[string]any) (string, error) {
	path := h.path(fmt.Sprint(body["id"]))
	encoded, err := json.Marshal(body)
	if err != nil {
		return path, err
	}
	return path, deskfiles.Replace(path, encoded)
}

// Sent is a held log that the service has now answered: stored, or refused.
type Sent struct {
	Body    map[string]any
	Logged  service.Logged
	Refused error
}

// SendHeld sends each held log again, oldest first, because an id begins with
// its time. It stops at the first one that gets no answer, and answers that
// error beside what it sent.
func (h HeldLogs) SendHeld(ctx context.Context, client *service.Client) ([]Sent, error) {
	entries, err := os.ReadDir(h.Dir)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	names := []string{}
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".json") && !strings.HasPrefix(entry.Name(), ".") {
			names = append(names, entry.Name())
		}
	}
	slices.Sort(names)
	var sent []Sent
	for _, name := range names {
		held, err := os.ReadFile(filepath.Join(h.Dir, name))
		if err != nil {
			return sent, err
		}
		var body map[string]any
		if err := json.Unmarshal(held, &body); err != nil {
			return sent, fmt.Errorf("the held log %s cannot be read: %w", filepath.Join(h.Dir, name), err)
		}
		logged, err := h.Post(ctx, client, body)
		var stillHeld *HeldError
		if errors.As(err, &stillHeld) {
			return sent, err
		}
		sent = append(sent, Sent{Body: body, Logged: logged, Refused: err})
	}
	return sent, nil
}
