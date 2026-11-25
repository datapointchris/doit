package desknudge

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/datapointchris/doit/cli/internal/service"
)

// The socket speaks HTTP/1.1 with JSON bodies, as tailscaled's LocalAPI does
// for its CLI. Its directory is 0700, so only this user reaches it.
//
//	GET  /panel   200 the today step, as panel show --json prints it
//	POST /picks   {"key": "...", "minutes": 30 | null}
//	              200 the step the pick leads to: done, or a timed pursuit's minutes
//	              409 refused, 502 the completion failed, 504 no answer came,
//	              each {"exit_code": 2 | 1 | 4, "message": "..."}

// pickRequest is what POST /picks takes.
type pickRequest struct {
	Key     string `json:"key"`
	Minutes *int   `json:"minutes"`
}

type server struct {
	http *http.Server
}

func (s *server) stop(ctx context.Context) { _ = s.http.Shutdown(ctx) }

// serve listens on the socket. The watch lock is held, so a socket file left
// there belongs to a watch that is gone, and is removed.
func (w *Watch) serve(ctx context.Context) (*server, error) {
	path := w.desk.Paths.Socket
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return nil, err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	listener, err := (&net.ListenConfig{}).Listen(ctx, "unix", path)
	if err != nil {
		return nil, fmt.Errorf("listen on %s: %w", path, err)
	}
	routes := http.NewServeMux()
	routes.HandleFunc("GET /panel", func(response http.ResponseWriter, _ *http.Request) {
		answer(response, http.StatusOK, panelFor(w.desk.Paths.readPanel(), w.desk.Paths.readState(), StepToday))
	})
	routes.HandleFunc("POST /picks", func(response http.ResponseWriter, request *http.Request) {
		var asked pickRequest
		if err := json.NewDecoder(io.LimitReader(request.Body, 1<<16)).Decode(&asked); err != nil || asked.Key == "" {
			answer(response, http.StatusBadRequest, PickError{ExitCode: PickRefused, Message: "a pick names its key, as {\"key\": \"...\"}"})
			return
		}
		// A pick that started runs to its end, though the asker leaves.
		step, err := w.Pick(context.WithoutCancel(request.Context()), asked.Key, asked.Minutes)
		if err != nil {
			failure := pickFailure(err)
			answer(response, statusFor(failure.ExitCode), failure)
			return
		}
		answer(response, http.StatusOK, step)
	})
	served := &server{http: &http.Server{Handler: routes, ReadHeaderTimeout: 5 * time.Second}}
	go func() {
		if err := served.http.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("the socket stopped answering: " + err.Error())
		}
	}()
	return served, nil
}

func pickFailure(err error) PickError {
	var failed *PickError
	if errors.As(err, &failed) {
		return *failed
	}
	return PickError{ExitCode: service.ExitCode(err), Message: err.Error()}
}

func statusFor(exitCode int) int {
	switch exitCode {
	case PickRefused:
		return http.StatusConflict
	case service.NoAnswerExit:
		return http.StatusGatewayTimeout
	}
	return http.StatusBadGateway
}

func answer(response http.ResponseWriter, status int, body any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(body)
}

// ErrNoWatch is no watch answering on the socket.
var ErrNoWatch = errors.New("no desk nudge watch is running")

// ErrWatchServesNoPicks is a watch that holds this desk and answers nothing on
// the socket, such as an older doit's Python watch still running through an upgrade.
var ErrWatchServesNoPicks = errors.New("a watch holds this desk and answers no pick")

// pickWithin bounds one pick from the CLI. The watch bounds each request and
// program a pick makes, so only a pick that never ends reaches it.
const pickWithin = 5 * time.Minute

func socketClient(path string) *http.Client {
	return &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", path)
		},
	}}
}

// AskPick hands a pick to the watch, and answers the step it leads to, a
// *PickError where the pick did nothing or did not go through, or ErrNoWatch.
// Where nothing answers and another process holds the watch lock, it is
// ErrWatchServesNoPicks.
func (p Paths) AskPick(ctx context.Context, key string, minutes *int) (Panel, error) {
	ctx, cancel := context.WithTimeout(ctx, pickWithin)
	defer cancel()
	body, err := json.Marshal(pickRequest{Key: key, Minutes: minutes})
	if err != nil {
		return Panel{}, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://doit-watch/picks", bytes.NewReader(body))
	if err != nil {
		return Panel{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := socketClient(p.Socket).Do(request)
	if err != nil {
		var dialed *net.OpError
		if errors.As(err, &dialed) && dialed.Op == "dial" {
			if p.watchHeld() {
				return Panel{}, fmt.Errorf("%w: nothing answers on %s", ErrWatchServesNoPicks, p.Socket)
			}
			return Panel{}, fmt.Errorf("%w: nothing answers on %s", ErrNoWatch, p.Socket)
		}
		return Panel{}, &service.NoAnswer{Message: "the desk nudge watch gave no answer to the pick: " + err.Error()}
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		var failure PickError
		if json.NewDecoder(response.Body).Decode(&failure) != nil || failure.Message == "" {
			return Panel{}, &PickError{ExitCode: PickFailed, Message: fmt.Sprintf("the desk nudge watch answered HTTP %d", response.StatusCode)}
		}
		return Panel{}, &failure
	}
	var step Panel
	if err := json.NewDecoder(response.Body).Decode(&step); err != nil {
		return Panel{}, fmt.Errorf("the desk nudge watch answered a step this doit does not read: %w", err)
	}
	return step, nil
}

// Show is today's step, read from the panel the watch keeps and the state file, with no request.
func (d *Desk) Show() Panel {
	return panelFor(d.Paths.readPanel(), d.Paths.readState(), StepToday)
}
