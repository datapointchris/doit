// Package desknudge is the desk nudge: the clock that counts toward the
// interval, the files and locks it keeps on this desk, the panel it builds from
// the service's desk panel view, and the picks that complete a row.
//
// The service computes the interval and the answer counts from every answer,
// and every rule behind the panel's rows. This package holds only the desk's
// own rules: the clock, kept blind to input, the picks, the files and the locks.
package desknudge

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"
)

// StateVersion is the state file's shape. It stays 2 with the Python watch's
// field names, so either watch reads the other's clock: an older doit's Python
// watch after a revert, and this watch after an upgrade.
const StateVersion = 2

const (
	startingIntervalMinutes = 30
	tickEvery               = 15 * time.Second
	// tickCap is the most time one tick counts. A longer gap between ticks, such
	// as a suspend or a stopped watch, goes uncounted.
	tickCap    = 2 * tickEvery
	readyFor   = 10 * time.Minute
	staleAfter = 4 * tickEvery
	// rememberedNudges is how many ended nudges the state keeps, so a late answer can name its firing.
	rememberedNudges = 20
)

// Phase is where the clock stands. Only counting adds time.
type Phase string

const (
	Counting Phase = "counting"
	Firing   Phase = "firing"
	Ready    Phase = "ready"
	Paused   Phase = "paused"
	NoDesk   Phase = "no_desk"
)

// holdsNudge is whether a nudge is in hand, which beginFiring starts and endReady ends.
func (p Phase) holdsNudge() bool { return p == Firing || p == Ready }

// The answers a person gives a nudge. Timing and bother are separate axes, and
// the service decides what each does to the interval.
var answerValues = []string{"good_timing", "too_soon", "too_late", "bothered"}

// State is the state file, $XDG_STATE_HOME/doit/desk-nudge.json.
type State struct {
	Version         int            `json:"version"`
	Phase           Phase          `json:"phase"`
	CountedSeconds  float64        `json:"counted_seconds"`
	LastTick        *string        `json:"last_tick"`
	IntervalMinutes float64        `json:"interval_minutes"`
	AnswerCounts    map[string]int `json:"answer_counts"`
	Nudge           *Nudge         `json:"nudge"`
	QueuedAnswers   []QueuedAnswer `json:"queued_answers"`
	RecentNudges    []RecentNudge  `json:"recent_nudges"`
	Panel           PanelState     `json:"panel"`
	Pill            Pill           `json:"pill"`
	// PendingPicks are the page's clicks that have not ended, in click order.
	PendingPicks []PendingPick `json:"pending_picks"`
}

// Nudge is the nudge in hand, from its firing until an answer, a pick or its expiry ends it.
type Nudge struct {
	NudgeID         string      `json:"nudge_id"`
	FiredAt         string      `json:"fired_at"`
	CountedMinutes  float64     `json:"counted_minutes"`
	IntervalMinutes float64     `json:"interval_minutes"`
	Suggestion      *Suggestion `json:"suggestion"`
	ReadyUntil      *string     `json:"ready_until"`
}

// Suggestion is what a firing suggested: the pursuit the draw offered first, or
// why none was drawn. Each marshals as its own shape, which the record and status
// schemas describe.
type Suggestion struct {
	Pursuit string
	ItemID  *string
	Label   string
	Error   string
	Message string
	Detail  string
}

type drawnSuggestion struct {
	Pursuit string  `json:"pursuit"`
	ItemID  *string `json:"item_id"`
	Label   string  `json:"label"`
}

type failedSuggestion struct {
	Error   string `json:"error"`
	Message string `json:"message"`
	Detail  string `json:"detail"`
}

func (s Suggestion) MarshalJSON() ([]byte, error) {
	if s.Error != "" {
		return json.Marshal(failedSuggestion{s.Error, s.Message, s.Detail})
	}
	return json.Marshal(drawnSuggestion{s.Pursuit, s.ItemID, s.Label})
}

func (s *Suggestion) UnmarshalJSON(data []byte) error {
	var both struct {
		drawnSuggestion
		failedSuggestion
	}
	if err := json.Unmarshal(data, &both); err != nil {
		return err
	}
	*s = Suggestion{
		Pursuit: both.Pursuit, ItemID: both.ItemID, Label: both.Label,
		Error: both.Error, Message: both.Message, Detail: both.Detail,
	}
	return nil
}

// topic is the pill's word for a ready nudge: its pursuit, or break where none was drawn.
func (s *Suggestion) topic() string {
	if s == nil || s.Error != "" {
		return "break"
	}
	return s.Pursuit
}

// Why a firing has no suggestion, each with what the panel says instead.
const (
	Unreachable   = "unreachable"
	LoginRefused  = "login"
	NothingToDraw = "nothing_to_draw"
	Unreadable    = "unreadable"
)

var suggestionMessages = map[string]string{
	Unreachable:   "The doit service could not be reached, so take a break.",
	LoginRefused:  "doit refused this desk's login. Run `doit auth login`, and take a break.",
	NothingToDraw: "Nothing is due, so take a break.",
	Unreadable:    "The service's draw could not be read, so take a break.",
}

func failed(kind, detail string) *Suggestion {
	return &Suggestion{Error: kind, Message: suggestionMessages[kind], Detail: detail}
}

// AnswerRow is one answer as the service stores it.
type AnswerRow struct {
	NudgeID    string `json:"nudge_id"`
	Answer     string `json:"answer"`
	FiredAt    string `json:"fired_at"`
	AnsweredAt string `json:"answered_at"`
	Machine    string `json:"machine"`
}

func (r AnswerRow) storedAs() [2]string { return [2]string{r.NudgeID, r.Answer} }

// QueuedAnswer is an answer the service did not store, which the watch sends again.
type QueuedAnswer struct {
	Row  AnswerRow `json:"row"`
	Late bool      `json:"late"`
}

// RecentNudge is a nudge that ended lately, which a late answer can still name.
type RecentNudge struct {
	NudgeID string `json:"nudge_id"`
	FiredAt string `json:"fired_at"`
}

// PanelState holds each panel key picked, with when its completion ended. A
// read of the panel that begins after that lets it go. Ended holds how each
// pending pick ended, which the panel shows on its row.
type PanelState struct {
	Picked []HeldPick  `json:"picked"`
	Ended  []EndedPick `json:"ended"`
}

// HeldPick is a key picked, held until the parts are read again.
type HeldPick struct {
	Key string `json:"key"`
	At  string `json:"at"`
}

// Pill is what the waybar pill reads from the state file.
type Pill struct {
	Text       string  `json:"text"`
	Class      Phase   `json:"class"`
	StaleAfter *string `json:"stale_after"`
}

// freshState is a desk that never ran the watch: counting from zero at the starting interval.
func freshState() *State {
	state := &State{
		Version:         StateVersion,
		Phase:           Counting,
		IntervalMinutes: startingIntervalMinutes,
		AnswerCounts:    map[string]int{},
		QueuedAnswers:   []QueuedAnswer{},
		RecentNudges:    []RecentNudge{},
		Panel:           PanelState{Picked: []HeldPick{}, Ended: []EndedPick{}},
		PendingPicks:    []PendingPick{},
	}
	for _, answer := range answerValues {
		state.AnswerCounts[answer] = 0
	}
	state.drawPill()
	return state
}

// ErrUnreadableState is a state file that is not version 2's: not JSON, not an object, or another version.
var ErrUnreadableState = errors.New("unreadable state")

// parseState reads a state file's text over a fresh state, so a field it lacks reads as a fresh desk's.
func parseState(text []byte) (*State, error) {
	var head struct {
		Version *int `json:"version"`
	}
	if err := json.Unmarshal(text, &head); err != nil {
		return nil, fmt.Errorf("%w: it is not a JSON object (%v)", ErrUnreadableState, err)
	}
	if head.Version == nil || *head.Version != StateVersion {
		return nil, fmt.Errorf("%w: it holds no version %d state", ErrUnreadableState, StateVersion)
	}
	state := freshState()
	if err := json.Unmarshal(text, state); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnreadableState, err)
	}
	if state.QueuedAnswers == nil {
		state.QueuedAnswers = []QueuedAnswer{}
	}
	if state.RecentNudges == nil {
		state.RecentNudges = []RecentNudge{}
	}
	if state.Panel.Picked == nil {
		state.Panel.Picked = []HeldPick{}
	}
	if state.Panel.Ended == nil {
		state.Panel.Ended = []EndedPick{}
	}
	if state.PendingPicks == nil {
		state.PendingPicks = []PendingPick{}
	}
	return state, nil
}

// inHand is the nudge that is firing or ready, or nil where the phase holds none.
func (s *State) inHand() *Nudge {
	if s.Phase.holdsNudge() {
		return s.Nudge
	}
	return nil
}

// Change is what one change to the state asks of the effects around it.
type Change struct {
	Records []Record
	// Closes is the nudge this change ended.
	Closes *Nudge
	// Fire is present time reaching the interval, so a firing begins.
	Fire bool
}

// endReady ends the nudge in hand, restarts the clock at zero, and returns the nudge.
func (s *State) endReady() *Nudge {
	nudge := s.Nudge
	s.Nudge = nil
	s.CountedSeconds = 0
	s.Phase = Counting
	s.RecentNudges = append(s.RecentNudges, RecentNudge{nudge.NudgeID, nudge.FiredAt})
	if extra := len(s.RecentNudges) - rememberedNudges; extra > 0 {
		s.RecentNudges = s.RecentNudges[extra:]
	}
	return nudge
}

// tick is one tick of the watch: time counted, a ready nudge expired, the desk
// checked, and a firing called.
//
// A tick adds the time since the last one, capped at tickCap, and a longer gap
// writes gap. The clock holds while no desk session exists. Nothing but a nudge
// ending restarts it, so leaving the desk neither pauses nor resets it.
func (s *State) tick(now time.Time, liveDesk bool) Change {
	var change Change
	gap := time.Duration(0)
	if last, ok := instant(s.LastTick); ok {
		gap = max(now.Sub(last), 0)
	}
	s.LastTick = stampPtr(now)
	s.Pill.StaleAfter = stampPtr(now.Add(staleAfter))
	if gap > tickCap {
		change.Records = append(change.Records, newRecord(eventGap, now, "gap_seconds", rounded(gap.Seconds(), 3)))
	}
	phase := s.Phase
	if phase == Ready && s.Nudge != nil {
		if until, ok := instant(s.Nudge.ReadyUntil); ok && !now.Before(until) {
			change.Closes = s.endReady()
			change.Records = append(change.Records, newRecord(eventExpired, now, "nudge_id", change.Closes.NudgeID))
			phase = Counting
		}
	}
	switch {
	case phase == Paused || phase.holdsNudge():
		return change
	case !liveDesk:
		s.Phase = NoDesk
		return change
	case phase == NoDesk:
		s.Phase = Counting
		return change
	}
	if change.Closes == nil {
		s.CountedSeconds += min(gap, tickCap).Seconds()
	}
	change.Fire = s.CountedSeconds >= s.IntervalMinutes*60
	return change
}

// beginFiring is step one of a firing: the nudge minted and the phase firing,
// so a restarted watch finishes it rather than firing again.
func (s *State) beginFiring(now time.Time, nudgeID string) Nudge {
	s.Phase = Firing
	s.Nudge = &Nudge{
		NudgeID:         nudgeID,
		FiredAt:         stamp(now),
		CountedMinutes:  rounded(s.CountedSeconds/60, 2),
		IntervalMinutes: s.IntervalMinutes,
	}
	return *s.Nudge
}

// makeReady makes the nudge ready for readyFor where it is still the one
// firing, and reports whether it was.
func (s *State) makeReady(now time.Time, nudgeID string, suggestion *Suggestion) bool {
	if s.Phase != Firing || s.Nudge == nil || s.Nudge.NudgeID != nudgeID {
		return false
	}
	s.Nudge.Suggestion = suggestion
	s.Nudge.ReadyUntil = stampPtr(now.Add(readyFor))
	s.Phase = Ready
	return true
}

// pause holds the clock where it stands, ending a nudge in hand. A clock
// already paused is left alone.
func (s *State) pause(now time.Time) Change {
	if s.Phase == Paused {
		return Change{}
	}
	var change Change
	var nudgeID *string
	if s.inHand() != nil {
		change.Closes = s.endReady()
		nudgeID = &change.Closes.NudgeID
	}
	s.Phase = Paused
	change.Records = append(change.Records, newRecord(eventPaused, now, "nudge_id", nudgeID))
	return change
}

// resume carries on the count from where pause held it.
func (s *State) resume(now time.Time) Change {
	if s.Phase != Paused {
		return Change{}
	}
	s.Phase = Counting
	return Change{Records: []Record{newRecord(eventResumed, now)}}
}

// ErrNothingToAnswer is no nudge to answer: none is ready, or the id names none
// that this desk fired lately.
var ErrNothingToAnswer = errors.New("nothing to answer")

// taken is an answer taken on the desk: the row the service stores, whether
// its nudge had already ended, and the nudge it ends.
type taken struct {
	row    AnswerRow
	late   bool
	closes *Nudge
}

// takeAnswer takes an answer to the nudge in hand, or to a nudge that ended
// lately, which is late and leaves the clock alone. With no id it answers the
// nudge in hand.
func (s *State) takeAnswer(now time.Time, answer, nudgeID, machine string) (taken, error) {
	var t taken
	nudge := s.inHand()
	switch {
	case nudge != nil && (nudgeID == "" || nudgeID == nudge.NudgeID):
		t.row = AnswerRow{NudgeID: nudge.NudgeID, FiredAt: nudge.FiredAt}
		t.closes = s.endReady()
	case nudgeID == "":
		return t, fmt.Errorf("%w: no nudge is ready, so name one with --nudge", ErrNothingToAnswer)
	default:
		found := false
		for _, recent := range s.RecentNudges {
			if recent.NudgeID == nudgeID {
				t.row, found = AnswerRow{NudgeID: nudgeID, FiredAt: recent.FiredAt}, true
			}
		}
		if !found {
			return t, fmt.Errorf("%w: this desk fired no nudge %s lately", ErrNothingToAnswer, nudgeID)
		}
		t.late = true
	}
	t.row.Answer, t.row.AnsweredAt, t.row.Machine = answer, stamp(now), machine
	return t, nil
}

// Interval is the service's interval and answer counts, as GET /desk-nudge and
// an answer's reply carry them.
type Interval struct {
	IntervalMinutes float64        `json:"interval_minutes"`
	AnswerCounts    map[string]int `json:"answer_counts"`
}

// tookInterval is the interval the service answered, which the clock fires on from now.
func (s *State) tookInterval(answered Interval) {
	if answered.IntervalMinutes > 0 {
		s.IntervalMinutes = answered.IntervalMinutes
	}
	if answered.AnswerCounts != nil {
		s.AnswerCounts = answered.AnswerCounts
	}
}

// queued is the queued answer that the service would store in place of row.
func (s *State) queued(row AnswerRow) *QueuedAnswer {
	for i := range s.QueuedAnswers {
		if s.QueuedAnswers[i].Row.storedAs() == row.storedAs() {
			return &s.QueuedAnswers[i]
		}
	}
	return nil
}

// queue keeps an answer that the service did not store, so each tick sends it again.
func (s *State) queue(t taken) {
	if s.queued(t.row) == nil {
		s.QueuedAnswers = append(s.QueuedAnswers, QueuedAnswer{Row: t.row, Late: t.late})
	}
}

// unqueue drops a queued answer once stored or refused. False where another
// writer already dropped it, so only one writes its line.
func (s *State) unqueue(row AnswerRow) bool {
	kept := s.QueuedAnswers[:0:0]
	for _, entry := range s.QueuedAnswers {
		if entry.Row.storedAs() != row.storedAs() {
			kept = append(kept, entry)
		}
	}
	dropped := len(kept) != len(s.QueuedAnswers)
	s.QueuedAnswers = kept
	return dropped
}

// minutesLeft is the minutes still to count before a firing.
func (s *State) minutesLeft() int {
	return max(int(math.Ceil((s.IntervalMinutes*60-s.CountedSeconds)/60)), 0)
}

// drawPill sets the pill's text and class. The tick sets stale_after beside them.
func (s *State) drawPill() {
	switch s.Phase {
	case Ready:
		var suggestion *Suggestion
		if s.Nudge != nil {
			suggestion = s.Nudge.Suggestion
		}
		s.Pill.Text = "ready · " + suggestion.topic()
	case Counting, Firing:
		s.Pill.Text = fmt.Sprintf("⏲ %dm", s.minutesLeft())
	case Paused:
		s.Pill.Text = "paused"
	case NoDesk:
		s.Pill.Text = "no desk"
	}
	s.Pill.Class = s.Phase
}

// heldPicks are the picks that parts read at readAt may still list: each one
// whose completion ended at or after the read began.
func (s *State) heldPicks(readAt *string) []HeldPick {
	read, ok := instant(readAt)
	held := []HeldPick{}
	for _, pick := range s.Panel.Picked {
		at, _ := instant(&pick.At)
		if !ok || !at.Before(read) {
			held = append(held, pick)
		}
	}
	return held
}

// takePick holds a key as picked. False where it is held already, so one
// completion runs once. A pick whose completion ended before the parts were
// read is let go, because those parts no longer list what it completed.
func (s *State) takePick(readAt *string, key string, now time.Time) bool {
	s.Panel.Picked = s.heldPicks(readAt)
	for _, pick := range s.Panel.Picked {
		if pick.Key == key {
			return false
		}
	}
	s.Panel.Picked = append(s.Panel.Picked, HeldPick{Key: key, At: stamp(now)})
	return true
}

// settlePick holds a pick until a read that begins after its completion ended,
// or lets it go where the owner recorded nothing.
func (s *State) settlePick(key string, now time.Time, recordedNothing bool) {
	others := []HeldPick{}
	for _, pick := range s.Panel.Picked {
		if pick.Key != key {
			others = append(others, pick)
		}
	}
	if !recordedNothing {
		others = append(others, HeldPick{Key: key, At: stamp(now)})
	}
	s.Panel.Picked = others
}

// --- time ---

// stamp is an instant as the state file holds it: ISO 8601 to the millisecond, with its offset.
func stamp(moment time.Time) string { return moment.Format("2006-01-02T15:04:05.000-07:00") }

func stampPtr(moment time.Time) *string {
	text := stamp(moment)
	return &text
}

// recordStamp is an instant as a record holds it, in UTC to the millisecond.
func recordStamp(moment time.Time) string { return moment.UTC().Format("2006-01-02T15:04:05.000Z") }

func instant(text *string) (time.Time, bool) {
	if text == nil || *text == "" {
		return time.Time{}, false
	}
	moment, err := time.Parse(time.RFC3339Nano, *text)
	return moment, err == nil
}

func rounded(value float64, places int) float64 {
	scale := math.Pow(10, float64(places))
	return math.Round(value*scale) / scale
}
