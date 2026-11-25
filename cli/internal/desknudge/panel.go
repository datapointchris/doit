package desknudge

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"slices"
	"strings"
	"time"
)

// PanelSchemaVersion is the panel's shape, which schemas/desk-nudge-panel.schema.json describes.
const PanelSchemaVersion = 3

// The steps a panel document holds.
const (
	StepToday   = "today"
	StepMinutes = "minutes"
	StepDone    = "done"
)

// The kinds a row of the panel carries beyond what a completion completes.
const (
	KindNote          = "note"
	KindAnswer        = "answer"
	KindLogMinutes    = "log_minutes"
	KindAdjustMinutes = "adjust_minutes"
	// KindTick is a habit's checklist item, whose key records one set and
	// whose untick_key takes the latest set back.
	KindTick = "tick"
)

// The completion kinds that the watch completes by an owner of their own. The
// view's completion_kind definition holds the whole set, and every other kind
// is an entry of a review register.
const (
	KindPursuit = "pursuit"
	KindHabit   = "habit"
)

const (
	keySeparator = ":"
	minutesStep  = 5
	// minutesAdjustedFrom is where a minutes step's adjust rows count from where the pursuit has no usual amount.
	minutesAdjustedFrom = 30
	nothingLeft         = "nothing left today"
	// viewSchemaVersion is the version of the desk panel view that this doit reads.
	viewSchemaVersion = 1
)

// minutesWithoutUsual is what a timed pursuit's minutes step offers where no entry ever kept minutes.
var minutesWithoutUsual = []int{15, 30, 45, 60}

// View is the service's desk panel view, as schemas/desk-panel-view.schema.json
// describes it. It names the parts the watch reads, and keeps the document as
// the service answered it, so a part added to the service's answer reaches the
// panel with no change here.
type View struct {
	SchemaVersion    int             `json:"schema_version"`
	GeneratedAt      string          `json:"generated_at"`
	Suggestion       json.RawMessage `json:"suggestion"`
	SuggestionReason *string         `json:"suggestion_reason"`
	Answers          json.RawMessage `json:"answers"`
	Totals           json.RawMessage `json:"totals"`
	Pursuits         json.RawMessage `json:"pursuits"`
	Habits           json.RawMessage `json:"habits"`
	Lanes            json.RawMessage `json:"lanes"`
	Problems         json.RawMessage `json:"problems"`

	whole json.RawMessage
}

// UnmarshalJSON reads the parts and keeps the document whole.
func (v *View) UnmarshalJSON(text []byte) error {
	type parts View
	var decoded parts
	if err := json.Unmarshal(text, &decoded); err != nil {
		return err
	}
	*v = View(decoded)
	v.whole = append(json.RawMessage(nil), text...)
	return nil
}

// ViewVersionError is a desk panel view at a version that this doit does not read.
type ViewVersionError struct{ Version int }

func (e *ViewVersionError) Error() string {
	return fmt.Sprintf("the service's desk panel view is version %d, and this doit reads version %d", e.Version, viewSchemaVersion)
}

// Panel is one step of the desk nudge's panel: the view as the watch last read
// it, what only this desk knows, and the rows the step draws. The watch keeps
// the today step in desk-nudge-panel.json, `panel show --json` prints it, and
// `panel pick --json` prints the step a pick leads to.
type Panel struct {
	SchemaVersion int     `json:"schema_version"`
	Step          string  `json:"step"`
	Message       *string `json:"message"`
	// ReadAt is when the watch began the read that the view comes from, on this desk's clock.
	ReadAt *string `json:"read_at"`
	// GeneratedAt is when the service computed the view.
	GeneratedAt *string      `json:"generated_at"`
	StaleSince  *string      `json:"stale_since"`
	StaleReason *StaleReason `json:"stale_reason"`
	// View is the service's desk panel view whole, with its own schema_version, or null before the first read.
	View json.RawMessage `json:"view"`
	// Rows are what the step draws, in order, so no reader holds the rule that lists them.
	Rows    []Row        `json:"rows"`
	Nudge   *ReadyNudge  `json:"nudge"`
	Picked  []HeldPick   `json:"picked"`
	Minutes *MinutesStep `json:"minutes"`
	// Picks are the running picks, and how each one that ended came out, while the panel shows it.
	Picks []ShownPick `json:"picks"`
}

// StaleReason is why the latest read of the view failed.
type StaleReason struct {
	Error  string `json:"error"`
	Detail string `json:"detail"`
}

// ReadyNudge is the nudge that is ready, with the key that answers it for each answer.
type ReadyNudge struct {
	NudgeID    string        `json:"nudge_id"`
	FiredAt    string        `json:"fired_at"`
	ReadyUntil *string       `json:"ready_until"`
	Answers    []NudgeAnswer `json:"answers"`
}

// NudgeAnswer is one answer to the ready nudge, and the key that picks it.
type NudgeAnswer struct {
	Answer string `json:"answer"`
	Label  string `json:"label"`
	Key    string `json:"key"`
}

// MinutesStep is the timed pursuit that a minutes step logs. The step's rows log
// an amount, or reopen the step one adjustment above or below it.
type MinutesStep struct {
	Key  string `json:"key"`
	Text string `json:"text"`
}

// Completes is what picking an entry completes, as the view gives it.
type Completes struct {
	Kind   string  `json:"kind"`
	Ref    string  `json:"ref"`
	ItemID *string `json:"item_id"`
}

type choicePart struct {
	ID        *string    `json:"id"`
	Title     string     `json:"title"`
	Key       *string    `json:"key"`
	Completes *Completes `json:"completes"`
}

type suggestionPart struct {
	Pursuit       string       `json:"pursuit"`
	ItemID        *string      `json:"item_id"`
	Label         string       `json:"label"`
	Unit          string       `json:"unit"`
	WritesThrough bool         `json:"writes_through"`
	UsualMinutes  *float64     `json:"usual_minutes"`
	Key           *string      `json:"key"`
	Completes     *Completes   `json:"completes"`
	Choices       []choicePart `json:"choices"`
}

type pursuitsPart struct {
	Status string  `json:"status"`
	Reason *string `json:"reason"`
	Rows   []struct {
		Name          string       `json:"name"`
		Unit          string       `json:"unit"`
		Owed          bool         `json:"owed"`
		WritesThrough bool         `json:"writes_through"`
		UsualMinutes  *float64     `json:"usual_minutes"`
		Key           *string      `json:"key"`
		Completes     *Completes   `json:"completes"`
		Items         []choicePart `json:"items"`
		ItemsError    *string      `json:"items_error"`
	} `json:"rows"`
}

type habitsPart struct {
	Status string  `json:"status"`
	Reason *string `json:"reason"`
	Rows   []struct {
		Name      string          `json:"name"`
		Done      bool            `json:"done"`
		Key       *string         `json:"key"`
		Completes *Completes      `json:"completes"`
		Items     []checklistItem `json:"items"`
	} `json:"rows"`
}

type checklistItem struct {
	Name      string     `json:"name"`
	Target    int        `json:"target"`
	Ticks     int        `json:"ticks"`
	Key       *string    `json:"key"`
	UntickKey *string    `json:"untick_key"`
	Completes *Completes `json:"completes"`
}

type lanePart struct {
	Name   string  `json:"name"`
	Status string  `json:"status"`
	Reason *string `json:"reason"`
	Rows   []struct {
		Label     string     `json:"label"`
		Key       *string    `json:"key"`
		Completes *Completes `json:"completes"`
	} `json:"rows"`
}

type answerPart struct {
	Answer string `json:"answer"`
	Label  string `json:"label"`
}

// entry is one row of the panel that a pick completes, or a note that completes nothing.
type entry struct {
	key       string
	completes Completes
	// action is what a pick of the entry does: complete it, or tick or untick a checklist item.
	action    string
	text      string
	group     string
	unit      string
	usual     *float64
	suggested bool
	// ticks and target are a checklist item's sets today and its daily target.
	ticks, target *int
	// untickKey is the key that takes a checklist item's latest set back, on the entry that ticks it.
	untickKey string
}

func (e entry) note() bool  { return e.key == "" }
func (e entry) timed() bool { return e.unit == "minutes" }

// What a pick does, as its picked line names it.
const (
	ActionComplete = "complete"
	ActionTick     = "tick"
	ActionUntick   = "untick"
)

// holdsKey is whether a pick of action holds its key from a second pick until
// the rows are read again. A completion does. A tick or an untick records
// another set each time, so it holds nothing.
func holdsKey(action string) bool { return action == ActionComplete }

// setAction is whether action changes one set of a checklist item, which names the item.
func setAction(action string) bool { return action == ActionTick || action == ActionUntick }

// actionOf is what a pick of an entry does, where the entry names none.
func (e entry) actionOf() string {
	if e.action == "" {
		return ActionComplete
	}
	return e.action
}

// checklistEntries are a habit's checklist items, each as two entries: one
// ticks a set by the item's key, and one takes it back by the item's
// untick_key, both as the view names them. They stay listed once the habit is
// done, so a set can still be taken back.
func checklistEntries(habit string, items []checklistItem) []entry {
	var found []entry
	for _, item := range items {
		if item.Key == nil || item.UntickKey == nil || item.Completes == nil {
			continue
		}
		ticks, target := item.Ticks, item.Target
		tick := entry{
			key: *item.Key, completes: *item.Completes, action: ActionTick, text: habit + " · " + item.Name,
			group: "habits", ticks: &ticks, target: &target, untickKey: *item.UntickKey,
		}
		untick := tick
		untick.key, untick.action, untick.untickKey = *item.UntickKey, ActionUntick, ""
		found = append(found, tick, untick)
	}
	return found
}

func noteEntry(text string) entry { return entry{text: text} }

// parts decodes a raw part, leaving found at its zero value where the part is null.
func parts(raw json.RawMessage, found any) {
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, found)
	}
}

// read is whether the panel holds a read of the view.
func (p *Panel) read() bool { return p.GeneratedAt != nil }

// view is the panel's view, with each part null before the first read.
func (p *Panel) view() View {
	var found View
	parts(p.View, &found)
	return found
}

// entries are the parts' rows in the order the panel lists them: why the parts
// are old, the suggestion, then each owed pursuit, habit and lane row that a
// pick completes. A part that could not be read is named in a note, because a
// part dropped without a word reads as nothing owed.
func (p *Panel) entries() []entry {
	var found []entry
	if banner := p.banner(); banner != "" {
		found = append(found, noteEntry(banner))
	}
	if !p.read() {
		return found
	}
	view := p.view()
	var suggestion *suggestionPart
	parts(view.Suggestion, &suggestion)
	if suggestion != nil {
		found = append(found, suggestedEntries(suggestion)...)
	}
	var pursuits pursuitsPart
	parts(view.Pursuits, &pursuits)
	var habits habitsPart
	parts(view.Habits, &habits)
	var lanes []lanePart
	parts(view.Lanes, &lanes)
	if pursuits.Status == "unavailable" {
		found = append(found, noteEntry("pursuits unavailable — "+reasonOf(pursuits.Reason)))
	}
	if habits.Status == "unavailable" {
		found = append(found, noteEntry("habits unavailable — "+reasonOf(habits.Reason)))
	}
	for _, lane := range lanes {
		if lane.Status == "unavailable" {
			found = append(found, noteEntry(lane.Name+" unavailable — "+reasonOf(lane.Reason)))
		}
	}
	completing := 0
	for _, row := range pursuits.Rows {
		if !row.Owed {
			continue
		}
		if !row.WritesThrough {
			if row.Key != nil && row.Completes != nil {
				found = append(found, entry{key: *row.Key, completes: *row.Completes, text: row.Name, group: "pursuits", unit: row.Unit, usual: row.UsualMinutes})
				completing++
			}
			continue
		}
		if row.ItemsError != nil {
			found = append(found, noteEntry(fmt.Sprintf("%s unavailable — %s", row.Name, *row.ItemsError)))
			continue
		}
		listed := 0
		for _, item := range row.Items {
			if item.Key != nil && item.Completes != nil {
				found = append(found, entry{key: *item.Key, completes: *item.Completes, text: row.Name + " · " + choiceText(item), group: "pursuits", unit: row.Unit, usual: row.UsualMinutes})
				listed++
			}
		}
		if listed == 0 {
			found = append(found, noteEntry(row.Name+" has no choice to complete"))
		}
		completing += listed
	}
	for _, row := range habits.Rows {
		if !row.Done && row.Key != nil && row.Completes != nil {
			found = append(found, entry{key: *row.Key, completes: *row.Completes, text: row.Name, group: "habits"})
			completing++
		}
		found = append(found, checklistEntries(row.Name, row.Items)...)
	}
	for _, lane := range lanes {
		for _, row := range lane.Rows {
			if row.Key != nil && row.Completes != nil {
				text := row.Label
				if text == "" {
					text = row.Completes.Ref
				}
				found = append(found, entry{key: *row.Key, completes: *row.Completes, text: text, group: lane.Name})
				completing++
			}
		}
	}
	if completing == 0 {
		found = append(found, noteEntry(nothingLeft))
	}
	return onceEach(found)
}

// suggestedEntries lists the suggestion by the key that completes it. A pursuit
// that writes through is listed by the choice the draw settled, else by each
// choice that carries a key.
func suggestedEntries(s *suggestionPart) []entry {
	if s.Key != nil && s.Completes != nil {
		label := strings.Join(nonBlankLines(s.Label), " / ")
		text := s.Pursuit
		if label != "" {
			text += " · " + label
		}
		return []entry{{key: *s.Key, completes: *s.Completes, text: text, group: "pursuits", unit: s.Unit, usual: s.UsualMinutes, suggested: true}}
	}
	var all, settled []entry
	for _, choice := range s.Choices {
		if choice.Key == nil || choice.Completes == nil {
			continue
		}
		listed := entry{key: *choice.Key, completes: *choice.Completes, text: s.Pursuit + " · " + choiceText(choice), group: "pursuits", unit: s.Unit, usual: s.UsualMinutes, suggested: true}
		all = append(all, listed)
		if s.ItemID != nil && choice.ID != nil && *choice.ID == *s.ItemID {
			settled = append(settled, listed)
		}
	}
	if len(settled) > 0 {
		return settled
	}
	return all
}

func choiceText(choice choicePart) string {
	if choice.Title != "" {
		return choice.Title
	}
	if choice.ID != nil {
		return *choice.ID
	}
	return ""
}

func nonBlankLines(text string) []string {
	var lines []string
	for line := range strings.SplitSeq(text, "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func reasonOf(reason *string) string {
	if reason == nil || *reason == "" {
		return "no reason given"
	}
	return *reason
}

// onceEach lists each key once, where it first appears, because one completion is one row.
func onceEach(entries []entry) []entry {
	seen := map[string]bool{}
	var kept []entry
	for _, found := range entries {
		if found.note() || !seen[found.key] {
			kept = append(kept, found)
			seen[found.key] = true
		}
	}
	return kept
}

// banner is why the parts are old or missing, such as `rows from 14:05 — doit
// unreachable`. Empty where the latest read succeeded.
func (p *Panel) banner() string {
	if p.StaleReason == nil {
		if p.read() {
			return ""
		}
		return "no rows yet — the desk nudge watch reads them"
	}
	since := "no rows yet"
	if read, ok := instant(p.ReadAt); ok {
		since = "rows from " + read.Local().Format("15:04")
	}
	switch p.StaleReason.Error {
	case LoginRefused:
		return since + " — run doit auth login"
	case Unreadable:
		return since + " — doit answered in a shape this doit does not read"
	}
	return since + " — doit unreachable"
}

// find is the entry that key names among the parts, where one does and it completes something.
func (p *Panel) find(key string) (entry, bool) {
	for _, found := range p.entries() {
		if !found.note() && found.key == key {
			return found, true
		}
	}
	return entry{}, false
}

// held is whether key is picked since the parts were read.
func (p *Panel) held(key string) bool {
	for _, pick := range p.Picked {
		if pick.Key == key {
			return true
		}
	}
	return false
}

// Row is one line of a step as a terminal or a board draws it.
type Row struct {
	Key       *string `json:"key"`
	Kind      string  `json:"kind"`
	Text      string  `json:"text"`
	Group     *string `json:"group"`
	Minutes   *int    `json:"minutes"`
	Suggested bool    `json:"suggested"`
	// Ticks and Target are a checklist item's sets today, as the view was read, and its daily target.
	Ticks  *int `json:"ticks"`
	Target *int `json:"target"`
	// UntickKey takes a checklist item's latest set back.
	UntickKey *string `json:"untick_key"`
	// Picks are the running picks of the row's keys, and how each one that ended came out.
	Picks []ShownPick `json:"picks"`
}

// todayRows are what today's step draws, in order: the notes, the suggestion,
// the answers while a nudge is ready, then each row that a pick completes. A
// row whose key a pick holds leaves until the view is read again, unless its
// pick still commits, so a reader of rows never draws a row just completed as
// one to pick.
func (p *Panel) todayRows() []Row {
	entries := p.entries()
	rows := []Row{}
	at := 0
	if at < len(entries) && entries[at].note() && p.banner() != "" {
		rows = append(rows, rowOf(entries[at], p))
		at++
	}
	for at < len(entries) && entries[at].suggested {
		rows = append(rows, rowOf(entries[at], p))
		at++
	}
	if p.Nudge != nil {
		for _, answer := range p.Nudge.Answers {
			rows = append(rows, Row{Key: &answer.Key, Kind: KindAnswer, Text: answer.Label, Picks: []ShownPick{}})
		}
	}
	for _, found := range entries[at:] {
		if found.action != ActionUntick {
			rows = append(rows, rowOf(found, p))
		}
	}
	committing := func(pick ShownPick) bool { return pick.State == PickCommitting }
	kept := rows[:0]
	for _, row := range rows {
		if row.Key == nil || row.Kind == KindAnswer || !p.held(*row.Key) || slices.ContainsFunc(row.Picks, committing) {
			kept = append(kept, row)
		}
	}
	return kept
}

func rowOf(found entry, p *Panel) Row {
	if found.note() {
		return Row{Kind: KindNote, Text: found.text, Picks: []ShownPick{}}
	}
	key, group := found.key, found.group
	row := Row{Key: &key, Kind: found.completes.Kind, Text: found.text, Group: &group, Suggested: found.suggested, Ticks: found.ticks, Target: found.target}
	keys := []string{key}
	if found.action == ActionTick {
		row.Kind = KindTick
		row.Text += fmt.Sprintf(" · %d of %d sets", *found.ticks, *found.target)
		untick := found.untickKey
		row.UntickKey = &untick
		keys = append(keys, untick)
	}
	row.Picks = []ShownPick{}
	for _, pick := range p.Picks {
		if slices.Contains(keys, pick.Key) {
			row.Picks = append(row.Picks, pick)
		}
	}
	return row
}

// readyNudge is the ready nudge with each answer's key, its labels from the
// view's answers part. Before any read it labels each answer by its value.
func readyNudge(state *State, answersRaw json.RawMessage) *ReadyNudge {
	if state.Phase != Ready || state.Nudge == nil {
		return nil
	}
	var answers []answerPart
	parts(answersRaw, &answers)
	if len(answers) == 0 {
		for _, value := range answerValues {
			answers = append(answers, answerPart{Answer: value, Label: value})
		}
	}
	ready := &ReadyNudge{NudgeID: state.Nudge.NudgeID, FiredAt: state.Nudge.FiredAt, ReadyUntil: state.Nudge.ReadyUntil}
	for _, answer := range answers {
		ready.Answers = append(ready.Answers, NudgeAnswer{
			Answer: answer.Answer, Label: answer.Label,
			Key: strings.Join([]string{KindAnswer, answer.Answer, state.Nudge.NudgeID}, keySeparator),
		})
	}
	return ready
}

// panelFor is today's step or a done step, built over the cached view with what
// the state says now: the ready nudge, the keys picked since the view was read,
// and the picks. A done step draws no row.
func panelFor(cached *Panel, state *State, step string) Panel {
	built := Panel{}
	if cached != nil {
		built = *cached
	}
	built.SchemaVersion = PanelSchemaVersion
	built.Step = step
	built.Message = nil
	built.Minutes = nil
	built.Nudge = readyNudge(state, built.view().Answers)
	built.Picked = state.heldPicks(built.ReadAt)
	built.Picks = state.shownPicks(built.ReadAt)
	built.Rows = []Row{}
	if step == StepToday {
		built.Rows = built.todayRows()
	}
	return built
}

// withView is the cached panel with a view read at readAt.
func withView(cached *Panel, view *View, readAt time.Time) *Panel {
	built := Panel{}
	if cached != nil {
		built = *cached
	}
	built.ReadAt = stampPtr(readAt)
	built.GeneratedAt = &view.GeneratedAt
	built.StaleSince, built.StaleReason = nil, nil
	built.View = view.whole
	return &built
}

// withFailure is the cached panel kept as it was read, with why it could not be read again.
func withFailure(cached *Panel, kind, detail string, at time.Time) *Panel {
	built := Panel{}
	if cached != nil {
		built = *cached
	}
	if built.StaleSince == nil {
		built.StaleSince = stampPtr(at)
	}
	built.StaleReason = &StaleReason{Error: kind, Detail: detail}
	return &built
}

// readPanel is the panel the watch last wrote, or nil where it wrote none this release reads.
func (p Paths) readPanel() *Panel {
	text, err := os.ReadFile(p.Panel)
	if err != nil {
		return nil
	}
	var cached Panel
	if json.Unmarshal(text, &cached) != nil || cached.SchemaVersion != PanelSchemaVersion {
		return nil
	}
	return &cached
}

// minutesRows are a timed pursuit's minutes step: log minutes, or reopen the
// step minutesStep above or below it. Opened with none, it starts on the usual
// amount. With none usual, it offers minutesWithoutUsual, and its adjust rows
// count from minutesAdjustedFrom. No amount is ever taken from time away.
func minutesRows(found entry, minutes *int) []Row {
	name := found.completes.Ref
	usual := 0
	if found.usual != nil && *found.usual > 0 {
		usual = int(math.RoundToEven(*found.usual))
	}
	center := usual
	if minutes != nil {
		center = *minutes
	}
	amounts := minutesWithoutUsual
	around := minutesAdjustedFrom
	if center > 0 {
		amounts, around = []int{center}, center
	}
	offered := func(kind string, amount int, text string, suggested bool) Row {
		key := minutesKey(kind, amount, found.key)
		return Row{Key: &key, Kind: kind, Text: text, Minutes: &amount, Suggested: suggested, Picks: []ShownPick{}}
	}
	rows := []Row{}
	for _, amount := range amounts {
		text := fmt.Sprintf("%s · log %d min", name, amount)
		if amount == usual {
			text += " (usual)"
		}
		rows = append(rows, offered(KindLogMinutes, amount, text, amount == around))
	}
	for _, adjust := range []struct {
		sign   string
		amount int
	}{{"+", around + minutesStep}, {"−", around - minutesStep}} {
		if adjust.amount > 0 {
			rows = append(rows, offered(KindAdjustMinutes, adjust.amount, fmt.Sprintf("%s · %s%d → %d min", name, adjust.sign, minutesStep, adjust.amount), false))
		}
	}
	return rows
}

// minutesKey names the row it logs last, so that row's own key keeps its separators.
func minutesKey(kind string, minutes int, rowKey string) string {
	return fmt.Sprintf("%s%s%d%s%s", kind, keySeparator, minutes, keySeparator, rowKey)
}
