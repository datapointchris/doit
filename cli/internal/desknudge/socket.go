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

	"github.com/datapointchris/doit/cli/internal/panelpage"
	"github.com/datapointchris/doit/cli/internal/service"
)

// The socket speaks HTTP/1.1 with JSON bodies, as tailscaled's LocalAPI does
// for its CLI. Its directory is 0700, so only this user reaches it.
//
//	GET    /panel        today's step, as panel show --json prints it
//	POST   /picks        {"key": "...", "minutes": 30 | null}, which commits at once and answers how it ended
//	                     200 the step the pick leads to: done, or a timed pursuit's minutes
//	                     409 refused or skipped, 502 the completion failed, 504 no answer came
//	                     not safe to repeat: one sent while its key still runs is refused, and one
//	                     sent after is a second pick
//	POST   /page-codes   201 {"url", "expires_at"}: the panel page's URL with a one-use code
//	                     409 the watch serves no panel page until the service restarts
//
// A failure answers {"exit_code": 2 | 1 | 4, "message": "..."}. The panel page
// reaches every route but POST /page-codes under /api/, on 127.0.0.1 behind its
// Host check and cookie. A pick from the page that goes through is not
// notified, because the page's row shows it.

// pickRequest is what POST /picks takes.
type pickRequest struct {
	Key     string `json:"key"`
	Minutes *int   `json:"minutes"`
}

// PageCode is the panel page's URL carrying a one-use code, which opens the page until ExpiresAt.
type PageCode struct {
	URL       string `json:"url"`
	ExpiresAt string `json:"expires_at"`
}

type server struct {
	http *http.Server
	page *panelpage.Page
}

func (s *server) stop(ctx context.Context) {
	_ = s.http.Shutdown(ctx)
	if s.page != nil {
		s.page.Stop(ctx)
	}
}

// serve listens on the socket, and serves the panel page on 127.0.0.1. The
// watch lock is held, so a socket file left there belongs to a watch that is
// gone, and is removed. A page that cannot listen leaves the socket serving.
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
	page := panelpage.New(w.pageRoutes(false), w.desk.Now)
	unserved := ""
	if err := page.Listen(ctx); err != nil {
		logger.Error("the panel page is not served: " + err.Error())
		page, unserved = nil, err.Error()
	}
	routes := http.NewServeMux()
	routes.Handle("/", w.pageRoutes(true))
	routes.HandleFunc("POST /page-codes", func(response http.ResponseWriter, _ *http.Request) {
		if page == nil {
			answer(response, http.StatusConflict, PickError{
				ExitCode: PickFailed,
				Message:  "this watch serves no panel page, because it could not listen at its start (" + unserved + "). Restarting the desk-nudge user service tries again.",
			})
			return
		}
		url, expires := page.Mint()
		answer(response, http.StatusCreated, PageCode{URL: url, ExpiresAt: stamp(expires)})
	})
	served := &server{http: &http.Server{Handler: routes, ReadHeaderTimeout: 5 * time.Second}, page: page}
	go func() {
		if err := served.http.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("the socket stopped answering: " + err.Error())
		}
	}()
	return served, nil
}

// pageRoutes are what both the socket and the panel page answer: the panel and
// its picks. notifyDone is whether a pick that went through is notified.
func (w *Watch) pageRoutes(notifyDone bool) *http.ServeMux {
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
		step, err := w.Pick(context.WithoutCancel(request.Context()), asked.Key, asked.Minutes, notifyDone)
		if err != nil {
			failure := pickFailure(err)
			answer(response, statusFor(failure.ExitCode), failure)
			return
		}
		answer(response, http.StatusOK, step)
	})
	return routes
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
		return Panel{}, p.unanswered(err, "the pick")
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

// ErrWatchServesNoPage is a watch that answers on the socket and mints no
// code for the panel page, such as an older doit's Go watch still running
// through an upgrade.
var ErrWatchServesNoPage = errors.New("the desk nudge watch serves no panel page")

// pageCodeWithin bounds asking the watch for a code, which it mints with no request of its own.
const pageCodeWithin = 5 * time.Second

// AskPageCode asks the watch for the panel page's URL with a fresh one-use
// code. Where nothing answers, it is ErrNoWatch or ErrWatchServesNoPicks, as
// AskPick's is. Where the watch has no such route, or answers in a shape that
// this doit cannot read, it is ErrWatchServesNoPage. A refusal is a *PickError
// carrying the exit code the watch named, and why.
func (p Paths) AskPageCode(ctx context.Context) (PageCode, error) {
	ctx, cancel := context.WithTimeout(ctx, pageCodeWithin)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://doit-watch/page-codes", http.NoBody)
	if err != nil {
		return PageCode{}, err
	}
	response, err := socketClient(p.Socket).Do(request)
	if err != nil {
		return PageCode{}, p.unanswered(err, "the panel page's code")
	}
	defer func() { _ = response.Body.Close() }()
	var minted PageCode
	switch {
	case response.StatusCode == http.StatusNotFound:
		return PageCode{}, fmt.Errorf("%w: it answers 404 to POST /page-codes", ErrWatchServesNoPage)
	case response.StatusCode != http.StatusCreated:
		var failure PickError
		if json.NewDecoder(response.Body).Decode(&failure) != nil || failure.Message == "" {
			failure.Message = fmt.Sprintf("it answered HTTP %d to POST /page-codes", response.StatusCode)
		}
		if failure.ExitCode == 0 {
			failure.ExitCode = PickFailed
		}
		failure.Message = "the desk nudge watch minted no code for the panel page: " + failure.Message
		return PageCode{}, &failure
	case json.NewDecoder(response.Body).Decode(&minted) != nil || minted.URL == "":
		return PageCode{}, fmt.Errorf("%w: it answered a code this doit does not read", ErrWatchServesNoPage)
	}
	return minted, nil
}

// unanswered names why a request to the socket got no answer: no watch, a
// watch that answers nothing, or a watch that went quiet asked for what.
func (p Paths) unanswered(err error, what string) error {
	var dialed *net.OpError
	if errors.As(err, &dialed) && dialed.Op == "dial" {
		if p.watchHeld() {
			return fmt.Errorf("%w: nothing answers on %s", ErrWatchServesNoPicks, p.Socket)
		}
		return fmt.Errorf("%w: nothing answers on %s", ErrNoWatch, p.Socket)
	}
	return &service.NoAnswer{Message: "the desk nudge watch gave no answer to " + what + ": " + err.Error()}
}

// Show is today's step, read from the panel the watch keeps and the state file, with no request.
func (d *Desk) Show() Panel {
	return panelFor(d.Paths.readPanel(), d.Paths.readState(), StepToday)
}
