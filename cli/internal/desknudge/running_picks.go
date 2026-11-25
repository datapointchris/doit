package desknudge

import (
	"context"
	"fmt"
	"slices"
	"sync"
	"time"
)

// rememberedEndedPicks caps the refused and skipped picks kept for the panel, newest first.
const rememberedEndedPicks = 20

// RunningPick is a pick whose commit has begun and not ended. The state file
// holds it before its command runs, so a restart reads what may already have run.
type RunningPick struct {
	ID        string    `json:"id"`
	Key       string    `json:"key"`
	Text      string    `json:"text"`
	Action    string    `json:"action"`
	Completes Completes `json:"completes"`
	Minutes   *int      `json:"minutes"`
	// PickedAt is when it was picked, whose day on the desk the commit lands on.
	PickedAt  string `json:"picked_at"`
	StartedAt string `json:"started_at"`
	// Log is a pursuit's journal entry once its on_log is about to run, which a restart sends again under the same id.
	Log map[string]any `json:"log,omitempty"`
}

// EndedPick is how a pick came out, which the panel shows on its row. A done
// or uncertain one shows until a view read begins after it ended. A refused or
// skipped one shows until its key is picked again, or a view read begins on a
// later day.
type EndedPick struct {
	ID      string `json:"id"`
	Key     string `json:"key"`
	Text    string `json:"text"`
	Action  string `json:"action"`
	Minutes *int   `json:"minutes"`
	Outcome string `json:"outcome"`
	Message string `json:"message"`
	EndedAt string `json:"ended_at"`
}

// PickCommitting is the state of a pick whose commit runs. Once it ends, its state is its outcome.
const PickCommitting = "committing"

// ShownPick is a pick as the panel shows it: committing, or the outcome it ended with.
type ShownPick struct {
	ID      string  `json:"id"`
	Key     string  `json:"key"`
	Text    string  `json:"text"`
	Action  string  `json:"action"`
	State   string  `json:"state"`
	Minutes *int    `json:"minutes"`
	Message *string `json:"message"`
	EndedAt *string `json:"ended_at"`
}

func (p RunningPick) shown() ShownPick {
	return ShownPick{ID: p.ID, Key: p.Key, Text: p.Text, Action: p.Action, State: PickCommitting, Minutes: p.Minutes}
}

func (e EndedPick) shown() ShownPick {
	return ShownPick{
		ID: e.ID, Key: e.Key, Text: e.Text, Action: e.Action, State: e.Outcome, Minutes: e.Minutes,
		Message: &e.Message, EndedAt: &e.EndedAt,
	}
}

// heldUntilRead is whether an ended pick is kept only until the view is read again.
func (e EndedPick) heldUntilRead() bool {
	return e.Outcome == OutcomeDone || e.Outcome == OutcomeUncertain
}

func (s *State) runningPick(id string) *RunningPick {
	for i := range s.RunningPicks {
		if s.RunningPicks[i].ID == id {
			return &s.RunningPicks[i]
		}
	}
	return nil
}

func (s *State) dropRunning(id string) {
	s.RunningPicks = slices.DeleteFunc(s.RunningPicks, func(pick RunningPick) bool { return pick.ID == id })
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

// endPick keeps how a pick came out, beside the ended picks that the panel still lists.
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

// shownPicks are the running picks, then the ended ones still kept, each in the order picked.
func (s *State) shownPicks(readAt *string) []ShownPick {
	shown := []ShownPick{}
	for _, pick := range s.RunningPicks {
		shown = append(shown, pick.shown())
	}
	for _, ended := range s.endedKept(readAt) {
		shown = append(shown, ended.shown())
	}
	return shown
}

// pickTurns orders the picks that run. Every pick on one habit runs one at a
// time in the order it arrived, so its ticks and unticks land in click order,
// and every other pick waits only on its own key. A key that a pick already
// waits or runs on takes no second pick, so a double click is one pick.
type pickTurns struct {
	mu   sync.Mutex
	keys map[string]bool
	last map[string]chan struct{}
}

// turnTaken is the turn before a pick that waits on none.
var turnTaken = func() chan struct{} {
	taken := make(chan struct{})
	close(taken)
	return taken
}()

// enter queues a pick of key in group. It answers false where a pick already
// waits or runs on key. Otherwise it answers what closes once the pick before
// it in group has left, and leave, which the pick calls once it ends.
func (t *pickTurns) enter(group, key string) (before <-chan struct{}, leave func(), ok bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.keys == nil {
		t.keys, t.last = map[string]bool{}, map[string]chan struct{}{}
	}
	if t.keys[key] {
		return nil, nil, false
	}
	t.keys[key] = true
	before = turnTaken
	if last := t.last[group]; last != nil {
		before = last
	}
	mine := make(chan struct{})
	t.last[group] = mine
	return before, func() {
		t.mu.Lock()
		defer t.mu.Unlock()
		delete(t.keys, key)
		if t.last[group] == mine {
			delete(t.last, group)
		}
		close(mine)
	}, true
}

// commitGroup is the turns a pick of key waits in: one habit's, so its ticks
// and unticks land in the order clicked, or its key's own.
func commitGroup(target Completes, key string) string {
	if target.Kind == KindHabit {
		return KindHabit + keySeparator + target.Ref
	}
	return key
}

// resumeRunning takes up the picks that a stopped watch left running, each in
// its turn, under the restart rules in committed. Nobody waits for their
// answer, so a done one is not notified.
func (w *Watch) resumeRunning() {
	d := w.desk
	for _, pick := range d.Paths.readState().RunningPicks {
		before, leave, ok := w.turns.enter(commitGroup(pick.Completes, pick.Key), pick.Key)
		if !ok {
			continue
		}
		w.work.Go(func() {
			defer leave()
			<-before
			seen := takeSnapshot(w.commitCtx)
			d.endRunning(w.commitCtx, pick, d.committed(w.commitCtx, pick, true), seen, false, d.Now())
			w.readWanted.Store(true)
		})
	}
}

// committed runs a running pick through its owner, on the desk's day when it
// was picked, which a restart past midnight keeps. A habit is read again first,
// because life accepts a second completion. On a restart, a pick that was
// running follows these rules:
//
//	habit             read again, which skips a habit already done that day
//	pursuit           its saved entry sent again under the pick's id, which the service stores once; with none saved, nothing ran, so it runs whole
//	review, practice  marked done again on the day it was picked, which the service stores once
//	tick, untick      recorded uncertain and never run again, because life habits tick carries no key
func (d *Desk) committed(ctx context.Context, pick RunningPick, resumed bool) completion {
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
			return completion{summary: "doit: " + pick.Text + " not completed", body: said, exitCode: PickRefused, skipped: true}
		}
	case pick.Completes.Kind == KindPursuit && resumed && pick.Log != nil:
		return resentLog(d.postedLog(ctx, pick.Log), pick.Log)
	case pick.Completes.Kind == KindPursuit:
		return d.loggedPursuit(ctx, pick.Completes, pick.Minutes, logWrite{id: pick.ID, save: func(body map[string]any) error {
			return d.Paths.change(func(state *State) error {
				if saved := state.runningPick(pick.ID); saved != nil {
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

// endRunning ends a running pick as its outcome says, keeping how it came out
// for its row, and writes its picked line at at. A done one is notified only
// where notifyDone asks, because a row that shows it says so already.
func (d *Desk) endRunning(ctx context.Context, pick RunningPick, outcome completion, seen *snapshot, notifyDone bool, at time.Time) completion {
	readAt := d.Paths.panelReadAt()
	moment := d.Now()
	return d.finishPick(ctx, pickEnd{
		key: pick.Key, target: pick.Completes, action: pick.Action, minutes: pick.Minutes,
		holds: holdsKey(pick.Action), at: at, seen: seen, notifyDone: notifyDone,
		unwritten: "it stays marked started, and the watch takes it up again when it next starts",
		keep: func(state *State) {
			state.dropRunning(pick.ID)
			state.endPick(EndedPick{
				ID: pick.ID, Key: pick.Key, Text: pick.Text, Action: pick.Action, Minutes: pick.Minutes,
				Outcome: outcome.outcome(), Message: outcome.body, EndedAt: stamp(moment),
			}, readAt)
		},
	}, outcome)
}

// panelReadAt is when the read began that the cached panel comes from, or nil before any.
func (p Paths) panelReadAt() *string {
	if cached := p.readPanel(); cached != nil {
		return cached.ReadAt
	}
	return nil
}
