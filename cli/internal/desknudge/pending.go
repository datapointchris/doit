package desknudge

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"time"
)

// A click on the panel page is a pending pick: the watch holds it for
// pendingFor, and commits it once that ends, so a second click can take it
// back first. This is delayed commit, as an email client's undo send holds a
// message. The watch keeps the timer and the state file keeps the pick, so
// closing the page keeps the click.
const (
	pendingFor = 4 * time.Second
	// rememberedEndedPicks caps the refused and skipped picks kept for the panel, newest first.
	rememberedEndedPicks = 20
)

// PendingPick is one click waiting for its commit_at, or committing.
type PendingPick struct {
	ID        string    `json:"id"`
	Key       string    `json:"key"`
	Text      string    `json:"text"`
	Action    string    `json:"action"`
	Completes Completes `json:"completes"`
	Minutes   *int      `json:"minutes"`
	PickedAt  string    `json:"picked_at"`
	CommitAt  string    `json:"commit_at"`
	// StartedAt is when its commit began. A restart reads it to tell what may already have run.
	StartedAt *string `json:"started_at"`
	// Log is a pursuit's journal entry once its on_log is about to run, which a restart sends again under the same id.
	Log map[string]any `json:"log,omitempty"`
}

// EndedPick is how a pending pick came out, which the panel shows on its row.
// A done or uncertain one shows until a view read begins after it ended. A
// refused or skipped one shows until its key is picked again, or a view read
// begins on a later day. A canceled one is never drawn. It is kept until a
// read, so a second DELETE of it answers it again.
type EndedPick struct {
	ID       string `json:"id"`
	Key      string `json:"key"`
	Text     string `json:"text"`
	Action   string `json:"action"`
	Minutes  *int   `json:"minutes"`
	CommitAt string `json:"commit_at"`
	Outcome  string `json:"outcome"`
	Message  string `json:"message"`
	EndedAt  string `json:"ended_at"`
}

// The states of a pending pick before it ends. Once it ends, its state is its outcome.
const (
	PickPending    = "pending"
	PickCommitting = "committing"
)

// ShownPick is a pending pick as the panel, the status and the socket show it.
// Its state is pending, committing, or the outcome it ended with.
type ShownPick struct {
	ID       string  `json:"id"`
	Key      string  `json:"key"`
	Text     string  `json:"text"`
	Action   string  `json:"action"`
	State    string  `json:"state"`
	Minutes  *int    `json:"minutes"`
	CommitAt string  `json:"commit_at"`
	Message  *string `json:"message"`
	EndedAt  *string `json:"ended_at"`
}

func (p PendingPick) shown() ShownPick {
	state := PickPending
	if p.StartedAt != nil {
		state = PickCommitting
	}
	return ShownPick{ID: p.ID, Key: p.Key, Text: p.Text, Action: p.Action, State: state, Minutes: p.Minutes, CommitAt: p.CommitAt}
}

func (e EndedPick) shown() ShownPick {
	return ShownPick{
		ID: e.ID, Key: e.Key, Text: e.Text, Action: e.Action, State: e.Outcome, Minutes: e.Minutes,
		CommitAt: e.CommitAt, Message: &e.Message, EndedAt: &e.EndedAt,
	}
}

// heldUntilRead is whether an ended pick is kept only until the view is read again.
func (e EndedPick) heldUntilRead() bool {
	return e.Outcome == OutcomeDone || e.Outcome == OutcomeUncertain || e.Outcome == OutcomeCanceled
}

func (s *State) pendingPick(id string) *PendingPick {
	for i := range s.PendingPicks {
		if s.PendingPicks[i].ID == id {
			return &s.PendingPicks[i]
		}
	}
	return nil
}

// waitingOn is the pending pick of key, or nil where none waits.
func (s *State) waitingOn(key string) *PendingPick {
	for i := range s.PendingPicks {
		if s.PendingPicks[i].Key == key {
			return &s.PendingPicks[i]
		}
	}
	return nil
}

func (s *State) dropPending(id string) PendingPick {
	var dropped PendingPick
	s.PendingPicks = slices.DeleteFunc(s.PendingPicks, func(pick PendingPick) bool {
		if pick.ID == id {
			dropped = pick
			return true
		}
		return false
	})
	return dropped
}

// endedKept are the ended picks still kept over a view read at readAt.
func (s *State) endedKept(readAt *string) []EndedPick {
	read, readOnce := instant(readAt)
	return slices.DeleteFunc(slices.Clone(s.Panel.Ended), func(ended EndedPick) bool {
		at, _ := instant(&ended.EndedAt)
		if !readOnce || !at.Before(read) {
			return false
		}
		return ended.heldUntilRead() || at.Format(dayLayout) != read.Format(dayLayout)
	})
}

// forgetEnded drops the refused and skipped picks of key, because a new pick of key supersedes them.
func (s *State) forgetEnded(key string) {
	s.Panel.Ended = slices.DeleteFunc(s.Panel.Ended, func(ended EndedPick) bool {
		return ended.Key == key && !ended.heldUntilRead()
	})
}

// endPick keeps how a pending pick came out, beside those still kept.
func (s *State) endPick(ended EndedPick, readAt *string) {
	kept := append(s.endedKept(readAt), ended)
	lasting := 0
	for i := len(kept) - 1; i >= 0; i-- {
		if kept[i].heldUntilRead() {
			continue
		}
		if lasting++; lasting > rememberedEndedPicks {
			kept = slices.Delete(kept, i, i+1)
		}
	}
	s.Panel.Ended = kept
}

// shownPicks are the pending picks, then the ended ones still kept, each in
// click order. A canceled pick changed nothing, so none is drawn.
func (s *State) shownPicks(readAt *string) []ShownPick {
	shown := []ShownPick{}
	for _, pick := range s.PendingPicks {
		shown = append(shown, pick.shown())
	}
	for _, ended := range s.endedKept(readAt) {
		if ended.Outcome != OutcomeCanceled {
			shown = append(shown, ended.shown())
		}
	}
	return shown
}

// errUnchanged stops a change that found nothing to write.
var errUnchanged = errors.New("nothing to change")

// ErrNoPendingPick is an id that this desk holds no pick by now: it never held
// one, or the pick ended and a later read let it go.
var ErrNoPendingPick = errors.New("no pick")

// pendingEntry resolves a click to its row and its minutes, as a pick from a
// terminal resolves them. An answer and a minutes step's adjustment act at
// once, so a pending pick refuses them.
func (d *Desk) pendingEntry(panel *Panel, key string, minutes *int) (entry, *int, *PickError) {
	resolved, refusal := d.resolveKey(panel, key, minutes)
	switch {
	case refusal != nil:
		return entry{}, nil, refusal
	case resolved.verb == verbAnswer || resolved.verb == verbAdjust:
		return entry{}, nil, refused("%s acts at once, so it is picked through POST /picks", key)
	case resolved.verb == verbMinutes:
		return entry{}, nil, refused("%s is counted in minutes, so its pick names them", resolved.found.text)
	}
	return resolved.found, resolved.minutes, nil
}

// canceledEnd is how a pending pick taken back before its commit ends.
func canceledEnd(pick PendingPick, moment time.Time) EndedPick {
	return EndedPick{
		ID: pick.ID, Key: pick.Key, Text: pick.Text, Action: pick.Action, Minutes: pick.Minutes, CommitAt: pick.CommitAt,
		Outcome: OutcomeCanceled, Message: "taken back before it committed", EndedAt: stamp(moment),
	}
}

// CreatePending takes one click on the page: a pending pick that commits
// pendingFor from now. A click on a key whose pick still waits takes that pick
// back instead, and answers it canceled. The watch serializes the two, so a
// second click that arrives before the first one's answer still takes it back.
func (w *Watch) CreatePending(key string, minutes *int) (ShownPick, error) {
	d := w.desk
	moment := d.Now()
	panel := panelFor(d.Paths.readPanel(), d.Paths.readState(), StepToday)
	found, minutes, refusal := d.pendingEntry(&panel, key, minutes)
	if refusal != nil {
		return ShownPick{}, refusal
	}
	var made, taken *PendingPick
	var canceled *EndedPick
	err := d.Paths.change(func(state *State) error {
		if waiting := state.waitingOn(found.key); waiting != nil {
			if waiting.StartedAt != nil {
				refusal = refused("%s is committing already, so it can no longer be taken back", found.text)
				return errUnchanged
			}
			dropped := state.dropPending(waiting.ID)
			ended := canceledEnd(dropped, moment)
			state.endPick(ended, panel.ReadAt)
			taken, canceled = &dropped, &ended
			return nil
		}
		if holdsKey(found.actionOf()) && slices.ContainsFunc(state.heldPicks(panel.ReadAt), func(held HeldPick) bool { return held.Key == found.key }) {
			refusal = refused("%s is picked already, and is taken again once the watch reads the rows again", found.text)
			return errUnchanged
		}
		pick := PendingPick{
			ID: newID(moment), Key: found.key, Text: found.text, Action: found.actionOf(), Completes: found.completes,
			Minutes: minutes, PickedAt: stamp(moment), CommitAt: stamp(moment.Add(pendingFor)),
		}
		state.forgetEnded(found.key)
		state.PendingPicks = append(state.PendingPicks, pick)
		made = &pick
		return nil
	})
	switch {
	case refusal != nil:
		return ShownPick{}, refusal
	case err != nil:
		return ShownPick{}, &PickError{ExitCode: PickFailed, Message: "nothing was picked, because the desk nudge's state file could not be written: " + err.Error()}
	case canceled != nil:
		w.stopTimer(taken.ID)
		w.recordCancel(*taken)
		return canceled.shown(), nil
	}
	w.schedule(*made)
	return made.shown(), nil
}

// CancelPending takes back the pending pick id before it commits. A pick
// already taken back answers canceled again, so a resent DELETE reads as the
// first one did.
func (w *Watch) CancelPending(id string) (ShownPick, error) {
	d := w.desk
	moment := d.Now()
	var readAt *string
	if cached := d.Paths.readPanel(); cached != nil {
		readAt = cached.ReadAt
	}
	var taken PendingPick
	var canceled, again *EndedPick
	var refusal error
	err := d.Paths.change(func(state *State) error {
		pick := state.pendingPick(id)
		if pick == nil {
			refusal = fmt.Errorf("%w %s is pending on this desk, or ended lately", ErrNoPendingPick, id)
			for _, ended := range state.Panel.Ended {
				switch {
				case ended.ID != id:
				case ended.Outcome == OutcomeCanceled:
					again, refusal = &ended, nil
				default:
					refusal = refused("pending pick %s ended %s already, so it can no longer be taken back", id, ended.Outcome)
				}
			}
			return errUnchanged
		}
		if pick.StartedAt != nil {
			refusal = refused("%s is committing already, so it can no longer be taken back", pick.Text)
			return errUnchanged
		}
		taken = state.dropPending(id)
		ended := canceledEnd(taken, moment)
		state.endPick(ended, readAt)
		canceled = &ended
		return nil
	})
	switch {
	case refusal != nil:
		return ShownPick{}, refusal
	case again != nil:
		return again.shown(), nil
	case err != nil:
		return ShownPick{}, &PickError{ExitCode: PickFailed, Message: "nothing was taken back, because the desk nudge's state file could not be written: " + err.Error()}
	}
	w.stopTimer(id)
	w.recordCancel(taken)
	return canceled.shown(), nil
}

// recordCancel writes the cancel's picked line, off the asker's request,
// because the snapshot of the desk takes programs of its own.
func (w *Watch) recordCancel(pick PendingPick) {
	moment := w.desk.Now()
	w.work.Go(func() {
		seen := takeSnapshot(w.commitCtx)
		w.desk.Paths.appendRecords(pickedRecord(moment, nil, pickedFields{
			Kind: pick.Completes.Kind, Ref: pick.Completes.Ref, ItemID: pick.Completes.ItemID, Minutes: pick.Minutes,
			Action: ActionCancel, PendingPickID: &pick.ID,
		}, seen))
	})
}

// commitGroup is the picks whose commits run one at a time, in click order:
// every pick on one habit, so its ticks and unticks land in the order clicked,
// and every other pick on its own key.
func commitGroup(pick PendingPick) string {
	if pick.Completes.Kind == KindHabit {
		return KindHabit + keySeparator + pick.Completes.Ref
	}
	return pick.Key
}

// schedule starts a pending pick's own timer, which ends at its commit_at, off the watch's tick.
func (w *Watch) schedule(pick PendingPick) {
	commitAt, _ := instant(&pick.CommitAt)
	delay := max(commitAt.Sub(w.desk.Now()), 0)
	w.pending.Lock()
	defer w.pending.Unlock()
	if w.draining {
		return
	}
	w.timers[pick.ID] = w.after(delay, func() { w.due(pick) })
}

func (w *Watch) stopTimer(id string) {
	w.pending.Lock()
	defer w.pending.Unlock()
	if stop := w.timers[id]; stop != nil {
		stop()
	}
	delete(w.timers, id)
}

// due is a pending pick's timer ending, which starts its group's commits.
func (w *Watch) due(pick PendingPick) {
	w.pending.Lock()
	defer w.pending.Unlock()
	delete(w.timers, pick.ID)
	if w.draining {
		return
	}
	w.fired[pick.ID] = true
	w.startCommits(commitGroup(pick))
}

// startCommits runs the group's commits where none run yet. w.pending is held.
func (w *Watch) startCommits(group string) {
	if w.committing[group] {
		return
	}
	w.committing[group] = true
	w.work.Go(func() {
		for {
			w.pending.Lock()
			next := w.nextDue(group)
			if next == nil {
				delete(w.committing, group)
				w.pending.Unlock()
				return
			}
			w.taken[next.ID] = true
			w.pending.Unlock()
			w.desk.commitPending(w.commitCtx, *next)
			w.readWanted.Store(true)
		}
	})
}

// nextDue is the group's first pick in click order that this watch has not
// taken, where it is due: its timer ended, its commit_at passed, a restart
// found it started, or the watch drains. A first pick not yet due holds the
// ones after it, so the group keeps its click order. w.pending is held.
func (w *Watch) nextDue(group string) *PendingPick {
	now := w.desk.Now()
	for _, pick := range w.desk.Paths.readState().PendingPicks {
		if commitGroup(pick) != group || w.taken[pick.ID] {
			continue
		}
		commitAt, _ := instant(&pick.CommitAt)
		if w.fired[pick.ID] || w.draining || !now.Before(commitAt) {
			return &pick
		}
		return nil
	}
	return nil
}

// resumePending takes up the pending picks that a stopped watch left. An unstarted
// one commits at its commit_at. A started one is due now, and its commit
// follows the restart rules in committed.
func (w *Watch) resumePending() {
	left := w.desk.Paths.readState().PendingPicks
	w.pending.Lock()
	for _, pick := range left {
		if pick.StartedAt != nil {
			w.fired[pick.ID] = true
			w.startCommits(commitGroup(pick))
		}
	}
	w.pending.Unlock()
	for _, pick := range left {
		if pick.StartedAt == nil {
			w.schedule(pick)
		}
	}
}

// drainPending commits every pending pick now, its timer stopped, because the watch is exiting.
func (w *Watch) drainPending() {
	w.pending.Lock()
	defer w.pending.Unlock()
	w.draining = true
	for id, stop := range w.timers {
		stop()
		delete(w.timers, id)
	}
	for _, pick := range w.desk.Paths.readState().PendingPicks {
		w.startCommits(commitGroup(pick))
	}
}

// commitPending marks a pending pick started, runs it, and ends it. A pick
// taken back since it was due commits nothing.
func (d *Desk) commitPending(ctx context.Context, due PendingPick) {
	moment := d.Now()
	resumed := due.StartedAt != nil
	var readAt *string
	if cached := d.Paths.readPanel(); cached != nil {
		readAt = cached.ReadAt
	}
	var pick PendingPick
	gone, heldElsewhere := false, false
	err := d.Paths.change(func(state *State) error {
		current := state.pendingPick(due.ID)
		if current == nil {
			gone = true
			return errUnchanged
		}
		if holdsKey(current.Action) {
			if resumed {
				state.settlePick(current.Key, moment, false)
			} else if !state.takePick(readAt, current.Key, moment) {
				heldElsewhere = true
			}
		}
		if current.StartedAt == nil {
			current.StartedAt = stampPtr(moment)
		}
		pick = *current
		return nil
	})
	switch {
	case gone:
		return
	case err != nil:
		logger.Error(fmt.Sprintf("could not mark the pending pick of %s started, so it does not run until the watch starts again: %v", due.Key, err))
		notify(ctx, "doit: "+due.Text+" not picked", "the desk nudge's state file could not be written: "+err.Error())
		return
	}
	seen := takeSnapshot(ctx)
	var outcome completion
	if heldElsewhere {
		outcome = completion{summary: "doit: not picked", body: pick.Text + " was picked from a terminal first", exitCode: PickRefused, recordedNothing: true}
	} else {
		outcome = d.committed(ctx, pick, resumed)
	}
	holds := holdsKey(pick.Action) && !heldElsewhere
	d.endPending(ctx, pick, outcome, seen, readAt, holds)
}

// committed runs a started pending pick through its owner, on the desk's day
// when it was clicked, which a restart past midnight keeps. A habit is read
// again first, because life accepts a second completion. On a restart, a
// started pick follows these rules:
//
//	habit             read again, which skips a habit already done that day
//	pursuit           its saved entry sent again under the pick's id, which the service stores once; with none saved, nothing ran, so it runs whole
//	review, practice  marked done again on the day it was picked, which the service stores once
//	tick, untick      recorded uncertain and never run again, because life habits tick carries no key
func (d *Desk) committed(ctx context.Context, pick PendingPick, resumed bool) completion {
	found := entry{key: pick.Key, completes: pick.Completes, action: pick.Action, text: pick.Text}
	picked, _ := instant(&pick.PickedAt)
	day := picked.Format(dayLayout)
	switch {
	case resumed && setAction(pick.Action):
		return completion{
			summary: "doit: " + pick.Text + " may not have changed", exitCode: PickFailed,
			body: "the watch stopped while `" + shellJoin(setProgram(pick.Action, *pick.Completes.ItemID, day).argv) + "` ran, so whether it landed is not known, and it is not run again",
		}
	case pick.Action == ActionComplete && pick.Completes.Kind == KindHabit:
		done, err := habitDoneOn(ctx, pick.Completes.Ref, day)
		if err != nil {
			return completion{
				summary: "doit: habit not completed", exitCode: PickFailed, recordedNothing: true,
				body: "life's completions could not be read again, so " + pick.Text + " was not completed: " + err.Error(),
			}
		}
		if done {
			said := "already done on " + day
			if day == d.Now().Format(dayLayout) {
				said = "already done today"
			}
			return completion{summary: "doit: " + pick.Text + " " + said, body: said, exitCode: PickRefused, skipped: true}
		}
	case pick.Completes.Kind == KindPursuit && resumed && pick.Log != nil:
		return resentLog(d.postedLog(ctx, pick.Log), pick.Log)
	case pick.Completes.Kind == KindPursuit:
		return d.loggedPursuit(ctx, pick.Completes, pick.Minutes, logWrite{id: pick.ID, save: func(body map[string]any) error {
			return d.Paths.change(func(state *State) error {
				if saved := state.pendingPick(pick.ID); saved != nil {
					saved.Log = body
				}
				return nil
			})
		}})
	}
	return d.completed(ctx, found, pick.Minutes, logWrite{id: pick.ID}, picked)
}

// resentLog is a resent entry's outcome. One saved as its on_log began says
// on_log may not have run, because the watch stopped inside it.
func resentLog(posted completion, body map[string]any) completion {
	downstream, _ := body["downstream"].(map[string]any)
	if !posted.ok || downstream == nil || downstream["ran"] != nil {
		return posted
	}
	posted.ok = false
	posted.exitCode = PickFailed
	posted.summary = "doit: " + fmt.Sprint(body["pursuit"]) + " logged, and its on_log may not have run"
	posted.body += "\nThe watch stopped while `" + fmt.Sprint(downstream["command"]) + "` ran, so whether it ran is not known, and it is not run again"
	return posted
}

// endPending ends a pending pick as its outcome says, keeping how it came out
// for its row. A done one is not notified, because its row shows it. holds is
// whether the pick holds its key, which a pick from a terminal may hold instead.
func (d *Desk) endPending(ctx context.Context, pick PendingPick, outcome completion, seen *snapshot, readAt *string, holds bool) {
	moment := d.Now()
	d.finishPick(ctx, pickEnd{
		key: pick.Key, target: pick.Completes, action: pick.Action, minutes: pick.Minutes, pendingID: &pick.ID,
		holds: holds, at: moment, seen: seen, unwritten: "this pick commits again when the watch starts",
		keep: func(state *State) {
			state.dropPending(pick.ID)
			state.endPick(EndedPick{
				ID: pick.ID, Key: pick.Key, Text: pick.Text, Action: pick.Action, Minutes: pick.Minutes,
				CommitAt: pick.CommitAt, Outcome: outcome.outcome(), Message: outcome.body, EndedAt: stamp(moment),
			}, readAt)
		},
	}, outcome)
}
