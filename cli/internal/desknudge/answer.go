package desknudge

import (
	"context"
	"fmt"
	"slices"

	"github.com/datapointchris/doit/cli/internal/service"
)

// Answered is what became of one answer: taken on the desk, then stored by the service or not.
type Answered struct {
	row     AnswerRow
	late    bool
	saved   bool
	queued  bool
	failure error
	// interval is the interval the clock fires on after it.
	interval float64
	label    string
}

// IsAnswer is whether value is an answer a nudge takes.
func IsAnswer(value string) bool { return slices.Contains(answerValues, value) }

// Answers are the answers a nudge takes.
func Answers() []string { return slices.Clone(answerValues) }

// Outcome is the exit code and the sentence for one answer, which `doit
// desk-nudge answer` and a panel pick both give.
func (a Answered) Outcome() (int, string) {
	if a.saved {
		late := ""
		if a.late {
			late = " It answers a nudge that had already ended, so the clock is left alone."
		}
		return 0, fmt.Sprintf("%q is saved. The interval is %s minutes.%s", a.label, formatMinutes(a.interval), late)
	}
	kept := "It was refused, so nothing sends it again."
	if a.queued {
		kept = "It is kept on this desk, and the watch sends it again."
	}
	return service.ExitCode(a.failure), fmt.Sprintf("%q is not saved: %v. %s", a.label, a.failure, kept)
}

// labelOf is an answer's words, from the view's answers part as the watch last read it.
func (d *Desk) labelOf(answer string) string {
	if cached := d.Paths.readPanel(); cached != nil {
		var answers []answerPart
		parts(cached.Answers, &answers)
		for _, found := range answers {
			if found.Answer == answer {
				return found.Label
			}
		}
	}
	return answer
}

// Answer takes one answer, ends the nudge it answers, and stores it, keeping it
// to send again where a later send could store it.
//
// It is the one path for every answer to a nudge. With no id it answers the
// ready nudge. It answers ErrNothingToAnswer where none is ready and the id
// names none that this desk fired. An answer already kept on this desk is sent
// as it was kept, because that row is the one the service stores. One saved
// line is written per answer, by the writer that drops it from the queue or
// whose send the service added.
func (d *Desk) Answer(ctx context.Context, answer, nudgeID string) (Answered, error) {
	var took taken
	var kept *QueuedAnswer
	err := d.Paths.change(func(state *State) error {
		var takeErr error
		took, takeErr = state.takeAnswer(d.Now(), answer, nudgeID, d.Machine)
		if takeErr != nil {
			return takeErr
		}
		if found := state.queued(took.row); found != nil {
			copied := *found
			kept = &copied
		}
		return nil
	})
	if err != nil {
		return Answered{}, err
	}
	if kept != nil {
		took.row, took.late = kept.Row, kept.Late
	}
	var seen *snapshot
	if kept == nil {
		seen = takeSnapshot(ctx)
	}
	label := d.labelOf(answer)
	answered, sendErr := d.sendAnswer(ctx, took.row)
	if sendErr != nil {
		queued := service.LaterSendCanStore(sendErr)
		ended := false
		_ = d.Paths.change(func(state *State) error {
			if queued {
				state.queue(took)
			} else {
				ended = state.unqueue(took.row)
			}
			return nil
		})
		if kept == nil || ended {
			d.Paths.appendRecords(answeredRecord(d.Now(), took.row, took.late, false, queued, seen))
		}
		if queued {
			notify(ctx, "doit: answer not saved yet", fmt.Sprintf("%q is kept on this desk, and the watch sends it again.", label))
		} else {
			notify(ctx, "doit: answer refused", fmt.Sprintf("%q: %v", label, sendErr))
		}
		return Answered{row: took.row, late: took.late, queued: queued, failure: sendErr, label: label}, nil
	}
	ended := false
	var interval float64
	_ = d.Paths.change(func(state *State) error {
		ended = state.unqueue(took.row)
		state.tookInterval(answered.Interval)
		interval = state.IntervalMinutes
		return nil
	})
	// An answer that the service already held has its line from whoever stored it.
	if ended || (kept == nil && (answered.Added == nil || *answered.Added)) {
		d.Paths.appendRecords(answeredRecord(d.Now(), took.row, took.late, true, false, seen))
	}
	return Answered{row: took.row, late: took.late, saved: true, interval: interval, label: label}, nil
}

// resendQueued sends each queued answer again, in order, until a send fails in
// a way that a later send could outlast. The send that stores one writes
// answered with saved true. One that the service refuses is dropped and
// written with saved false, so it holds up none of the answers behind it.
func (d *Desk) resendQueued(ctx context.Context) {
	var queued []QueuedAnswer
	_ = d.Paths.change(func(state *State) error {
		queued = slices.Clone(state.QueuedAnswers)
		return nil
	})
	for _, entry := range queued {
		answered, err := d.sendAnswer(ctx, entry.Row)
		if err != nil && service.LaterSendCanStore(err) {
			logger.Warn(fmt.Sprintf("%d desk nudge answer(s) still not saved: %v", len(queued), err))
			return
		}
		dropped := false
		_ = d.Paths.change(func(state *State) error {
			dropped = state.unqueue(entry.Row)
			if err == nil {
				state.tookInterval(answered.Interval)
			}
			return nil
		})
		if !dropped {
			continue
		}
		if err != nil {
			logger.Error(fmt.Sprintf("the service refused a kept %s answer, so it is dropped: %v", entry.Row.Answer, err))
		}
		d.Paths.appendRecords(answeredRecord(d.Now(), entry.Row, entry.Late, err == nil, false, nil))
	}
}

// Pause holds the clock where it stands, ending a nudge in hand, and reports
// whether it was running.
func (d *Desk) Pause() (bool, error) {
	var change Change
	err := d.Paths.change(func(state *State) error {
		change = state.pause(d.Now())
		return nil
	})
	d.Paths.appendRecords(change.Records...)
	return len(change.Records) > 0, err
}

// Resume carries on the count from where Pause held it, and reports whether it was paused.
func (d *Desk) Resume() (bool, error) {
	var change Change
	err := d.Paths.change(func(state *State) error {
		change = state.resume(d.Now())
		return nil
	})
	d.Paths.appendRecords(change.Records...)
	return len(change.Records) > 0, err
}
