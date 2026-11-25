package desknudge

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/datapointchris/doit/cli/internal/service"
)

const (
	// intervalReadEvery is how often the watch reads the interval, because another desk's answers move it too.
	intervalReadEvery = 300 * time.Second
	// panelReadEvery is how often the watch reads the panel's parts while the
	// desk is in use, beside each firing and each pick. A timed read sends no
	// draw, so it saves no draw and counts no offer.
	panelReadEvery = 300 * time.Second
	// drainWithin is how long an exit waits for a pick or a firing under way. It
	// has to stay under the stop timeout of the unit that runs the watch.
	drainWithin = 20 * time.Second
)

// Watch counts the time since the last nudge ended and fires the nudge at the
// interval. Each request and program runs off the tick, in a goroutine of its own.
type Watch struct {
	desk *Desk
	// Executable is the binary whose replacement ends the watch, so the unit starts the new one.
	Executable string

	work                      sync.WaitGroup
	firing, resending, asking atomic.Bool
	// reading keeps one read of the panel's parts at a time, so a slower read never writes over a newer one.
	reading    sync.Mutex
	readWanted atomic.Bool
	// timing guards when the interval and the parts were last read.
	timing       sync.Mutex
	intervalRead time.Time
	panelRead    time.Time
	started      binaryID

	// drainFor is how long an exit waits, drainWithin outside a test.
	drainFor time.Duration
	// commitCtx outlives the watch's stop, because a pick taken up at its start runs to its end.
	commitCtx context.Context
	turns     pickTurns
}

// NewWatch is a watch over desk.
func NewWatch(desk *Desk) *Watch {
	executable, _ := os.Executable()
	return &Watch{desk: desk, Executable: executable, drainFor: drainWithin, commitCtx: context.Background()}
}

// ErrAnotherWatch is a watch already running on this desk.
var ErrAnotherWatch = errors.New("another watch is running on this desk, so this one exits")

// Run holds the watch lock, serves the socket, and ticks until ctx ends or the
// binary is replaced. Either way it drains what is under way, then returns nil.
func (w *Watch) Run(ctx context.Context) error {
	lock, err := w.desk.Paths.HoldWatchLock()
	if errors.Is(err, ErrHeldElsewhere) {
		return ErrAnotherWatch
	}
	if err != nil {
		return err
	}
	defer func() { _ = lock.Close() }()
	w.started = identify(w.Executable)
	serving, err := w.serve(ctx)
	if err != nil {
		return err
	}
	w.Start(ctx)
	// The first tick comes at once, so the pill reads the watch as running from its start.
	w.Tick(ctx)
	ticker := time.NewTicker(tickEvery)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			logger.Info("stopped, so the watch drains what is under way and exits")
			return w.drain(serving)
		case <-ticker.C:
			w.Tick(ctx)
			if w.replaced() {
				logger.Info(w.Executable + " is another build now, so the watch exits for the unit to start it")
				return w.drain(serving)
			}
		}
	}
}

// drain stops the socket and the page, and waits for each pick and request
// under way, within drainFor. A pick still running at the deadline stays
// marked started, and the next watch follows the restart rules for it.
func (w *Watch) drain(serving *server) error {
	ctx, cancel := context.WithTimeout(context.Background(), w.drainFor)
	defer cancel()
	serving.stop(ctx)
	done := make(chan struct{})
	go func() { w.work.Wait(); close(done) }()
	select {
	case <-done:
	case <-ctx.Done():
		logger.Warn(fmt.Sprintf("work was still under way after %s, so the watch exits without it", w.drainFor))
	}
	return nil
}

func (w *Watch) background(busy *atomic.Bool, run func()) {
	if busy != nil && !busy.CompareAndSwap(false, true) {
		return
	}
	w.work.Go(func() {
		if busy != nil {
			defer busy.Store(false)
		}
		run()
	})
}

// Wait waits for every request and program under way.
func (w *Watch) Wait() { w.work.Wait() }

// Start writes started, then finishes a nudge that a stopped watch left firing,
// and takes up the picks it left running. A ready nudge stays on the pill, unchimed.
func (w *Watch) Start(ctx context.Context) {
	w.commitCtx = context.WithoutCancel(ctx)
	moment := w.desk.Now()
	var left *Nudge
	var started Record
	_ = w.desk.Paths.change(func(state *State) error {
		// A restarted watch keeps the last tick, so its first tick counts the time it was stopped as absence.
		if state.LastTick == nil {
			state.LastTick = stampPtr(moment)
		}
		if state.Phase == Firing && state.Nudge != nil {
			copied := *state.Nudge
			left = &copied
		}
		started = newRecord(eventStarted, moment,
			"doit_version", w.desk.Version,
			"phase", state.Phase,
			"counted_minutes", rounded(state.CountedSeconds/60, 2),
			"interval_minutes", state.IntervalMinutes,
		)
		return nil
	})
	w.desk.Paths.appendRecords(started)
	if left != nil {
		w.background(&w.firing, func() { w.fire(ctx, *left) })
	}
	w.resumeRunning()
	w.refreshIntervalWhenDue(ctx)
	w.readPanelWhenDue(ctx)
}

// Tick counts, expires, and starts whatever the tick calls for off the loop.
func (w *Watch) Tick(ctx context.Context) {
	moment := w.desk.Now()
	live := hyprlandRunning()
	var change Change
	var firing *Nudge
	queued := false
	_ = w.desk.Paths.change(func(state *State) error {
		change = state.tick(moment, live)
		if change.Fire {
			nudge := state.beginFiring(moment, service.NewWriteID(moment))
			firing = &nudge
		}
		queued = len(state.QueuedAnswers) > 0
		return nil
	})
	w.desk.Paths.appendRecords(change.Records...)
	if firing != nil {
		w.background(&w.firing, func() { w.fire(ctx, *firing) })
	}
	if queued {
		w.background(&w.resending, func() { w.desk.resendQueued(ctx) })
	}
	w.refreshIntervalWhenDue(ctx)
	w.readPanelWhenDue(ctx)
}

func (w *Watch) refreshIntervalWhenDue(ctx context.Context) {
	now := w.desk.Now()
	w.timing.Lock()
	due := w.intervalRead.IsZero() || now.Sub(w.intervalRead) >= intervalReadEvery
	if due {
		w.intervalRead = now
	}
	w.timing.Unlock()
	if !due {
		return
	}
	w.background(&w.asking, func() {
		answered, err := w.desk.readInterval(ctx)
		if err != nil {
			logger.Warn("kept the interval this desk last read, because the service gave none: " + err.Error())
			return
		}
		_ = w.desk.Paths.change(func(state *State) error {
			state.tookInterval(answered)
			return nil
		})
	})
}

// readPanelWhenDue reads the parts again after a pick, with a draw, and every
// panelReadEvery without one. A timed read waits while logind marks the session
// idle, because the view asks every app. It is due again at the next tick, so
// the first tick back at the desk reads the parts.
func (w *Watch) readPanelWhenDue(ctx context.Context) {
	now := w.desk.Now()
	if w.readWanted.CompareAndSwap(true, false) {
		w.markPanelRead(now)
		w.background(nil, func() { _, _ = w.readPanel(ctx, true) })
		return
	}
	w.timing.Lock()
	due := w.panelRead.IsZero() || now.Sub(w.panelRead) >= panelReadEvery
	if due {
		w.panelRead = now
	}
	w.timing.Unlock()
	if !due {
		return
	}
	w.background(nil, func() {
		if sessionIdle(ctx) {
			w.markPanelRead(time.Time{})
			return
		}
		_, _ = w.readPanel(ctx, false)
	})
}

func (w *Watch) markPanelRead(at time.Time) {
	w.timing.Lock()
	defer w.timing.Unlock()
	w.panelRead = at
}

// readPanel reads the view and writes the panel. A failed read keeps the last
// parts and names why, so the panel reads as old rather than as nothing owed.
// It answers the view, or nil where the read failed, with the error.
func (w *Watch) readPanel(ctx context.Context, draw bool) (*View, error) {
	w.reading.Lock()
	defer w.reading.Unlock()
	began := w.desk.Now()
	view, err := w.desk.readView(ctx, draw)
	cached := w.desk.Paths.readPanel()
	if err != nil {
		logger.Warn("kept the panel's parts this desk last read, because the service gave none: " + err.Error())
		cached = withFailure(cached, failureKind(err), err.Error(), w.desk.Now())
	} else {
		cached = withView(cached, view, began)
	}
	written := panelFor(cached, w.desk.Paths.readState(), StepToday)
	if writeErr := replaceFile(w.desk.Paths.Panel, written); writeErr != nil {
		logger.Error("could not write the panel's parts: " + writeErr.Error())
	}
	return view, err
}

// fire reads the view with a draw, takes the snapshot, writes fired, makes the
// nudge ready, chimes, and writes the panel under its suggestion. Ready is the
// pill's class that flashes, so the chime and the flash begin together.
func (w *Watch) fire(ctx context.Context, nudge Nudge) {
	w.markPanelRead(w.desk.Now())
	view, err := w.readPanel(ctx, true)
	suggestion := suggestionFrom(view, err)
	seen := takeSnapshot(ctx)
	firedAt, _ := instant(&nudge.FiredAt)
	fired := newRecord(eventFired, firedAt,
		"nudge_id", nudge.NudgeID,
		"interval_minutes", nudge.IntervalMinutes,
		"counted_minutes", nudge.CountedMinutes,
		"suggestion", suggestion,
	)
	seen.addTo(&fired)
	w.desk.Paths.appendRecords(fired)
	ready := false
	_ = w.desk.Paths.change(func(state *State) error {
		ready = state.makeReady(w.desk.Now(), nudge.NudgeID, suggestion)
		return nil
	})
	if ready {
		chime(ctx)
		w.rewritePanel()
	}
}

// rewritePanel writes the panel again over its cached parts, for a change in the nudge.
func (w *Watch) rewritePanel() {
	w.reading.Lock()
	defer w.reading.Unlock()
	written := panelFor(w.desk.Paths.readPanel(), w.desk.Paths.readState(), StepToday)
	if err := replaceFile(w.desk.Paths.Panel, written); err != nil {
		logger.Error("could not write the panel's parts: " + err.Error())
	}
}

// suggestionFrom is what a firing suggests: the view's suggestion, or why there is none.
func suggestionFrom(view *View, err error) *Suggestion {
	if err != nil {
		return failed(failureKind(err), err.Error())
	}
	var drawn *suggestionPart
	parts(view.Suggestion, &drawn)
	if drawn == nil {
		return failed(NothingToDraw, reasonOf(view.SuggestionReason))
	}
	return &Suggestion{Pursuit: drawn.Pursuit, ItemID: drawn.ItemID, Label: drawn.Label}
}

// Pick runs one pick for the socket or the page, and asks for the parts to be
// read again once it ran. notifyDone is whether a pick that went through is
// notified, which the page's own row says already.
func (w *Watch) Pick(ctx context.Context, key string, minutes *int, notifyDone bool) (Panel, error) {
	var answered Panel
	var err error
	done := make(chan struct{})
	w.work.Go(func() {
		defer close(done)
		answered, err = w.pick(ctx, key, minutes, notifyDone)
		w.readPanelWhenDue(ctx)
	})
	<-done
	return answered, err
}

// binaryID is what tells one build of the binary from another: the file's
// identity, and its digest once that changes.
type binaryID struct {
	file   os.FileInfo
	digest [sha256.Size]byte
}

func identify(path string) binaryID {
	if path == "" {
		return binaryID{}
	}
	info, err := os.Stat(path)
	if err != nil {
		return binaryID{}
	}
	found := binaryID{file: info}
	if file, openErr := os.Open(path); openErr == nil {
		hash := sha256.New()
		_, _ = io.Copy(hash, file)
		_ = file.Close()
		copy(found.digest[:], hash.Sum(nil))
	}
	return found
}

// replaced is whether the binary at Executable is another build than the one
// this watch started from. Its digest is read only when the file's identity
// changed, so an install that writes the same bytes restarts nothing.
func (w *Watch) replaced() bool {
	if w.Executable == "" || w.started.file == nil {
		return false
	}
	info, err := os.Stat(w.Executable)
	if err != nil {
		return false
	}
	was := w.started.file
	if os.SameFile(info, was) && info.Size() == was.Size() && info.ModTime().Equal(was.ModTime()) {
		return false
	}
	now := identify(w.Executable)
	if now.digest == w.started.digest {
		w.started = now
		return false
	}
	return true
}
