package service

import "context"

// LogCheck is POST /journal/done's answer to a check, which writes nothing.
type LogCheck struct {
	// Pursuit is the one pursuit that the typed name matched.
	Pursuit string `json:"pursuit"`
	// OccurredAt is the instant the log records, which the service resolved from what was typed.
	OccurredAt string `json:"occurred_at"`
	// OnLog is the template that completes the choice in its app, or nil.
	OnLog *string `json:"on_log"`
	// MinutesRefusal is why the log still needs minutes, where it does.
	MinutesRefusal *string `json:"minutes_refusal"`
	// Choices are the pursuit's current choices, each with its line and the item a log records.
	Choices []LogChoice `json:"choices"`
	// Chosen is the choice that the log is about, flattened as a log records it, or nil.
	Chosen map[string]any `json:"chosen"`
	// Ask is several choices, none named, where only the person logging can say which.
	Ask bool `json:"ask"`
	// NamesChosen is whether the log names its choice whatever on_log does.
	NamesChosen bool `json:"names_chosen"`
}

// LogChoice is one of a pursuit's current choices.
type LogChoice struct {
	Line string         `json:"line"`
	Item map[string]any `json:"item"`
}

// LogAsked is what a check asks about a log, as it was typed.
type LogAsked struct {
	Pursuit string  `json:"pursuit"`
	Minutes *int    `json:"duration_minutes"`
	ItemID  *string `json:"item_id,omitempty"`
	// Ago is a span back from now, such as 90m, which the service resolves to the instant the log records.
	Ago string `json:"ago,omitempty"`
}

// CheckLog asks whether a log would be taken, writing nothing. A name that
// means no one pursuit is refused with 404, minutes, a span or an item that
// do not fit with 422, and an item of an app that could not be read with 409.
func (c *Client) CheckLog(ctx context.Context, asked LogAsked) (LogCheck, error) {
	body := struct {
		LogAsked
		Check bool `json:"check"`
	}{asked, true}
	var answer LogCheck
	err := c.Post(ctx, "/journal/done", body, true, &answer)
	return answer, err
}

// Logged is POST /journal/done's answer once it stores an entry.
type Logged struct {
	DueInDays *float64 `json:"due_in_days"`
	// Due is when the pursuit next comes due, as a person reads it, or nil without a schedule.
	Due *string `json:"due"`
}

// Log stores the journal entry in body. The service keeps an entry by its id,
// so a resend is stored once.
func (c *Client) Log(ctx context.Context, body map[string]any) (Logged, error) {
	var answer Logged
	err := c.Post(ctx, "/journal/done", body, true, &answer)
	return answer, err
}

// Skipped is POST /journal/skip's answer: how long the pass runs, and the pursuit it matched.
type Skipped struct {
	Days  int    `json:"days"`
	Until string `json:"until"`
	Entry struct {
		Pursuit string `json:"pursuit"`
	} `json:"entry"`
}

// Skip passes on the pursuit body names. The service keeps a pass by its id,
// so a resend is stored once.
func (c *Client) Skip(ctx context.Context, body map[string]any) (Skipped, error) {
	var answer Skipped
	err := c.Post(ctx, "/journal/skip", body, true, &answer)
	return answer, err
}

// Resumed is POST /journal/resume's answer: every pursuit whose skip it ended.
type Resumed struct {
	Resumed []string `json:"resumed"`
}

// Resume ends the standing skip of the pursuit body names, or of every one.
func (c *Client) Resume(ctx context.Context, body map[string]any) (Resumed, error) {
	var answer Resumed
	err := c.Post(ctx, "/journal/resume", body, false, &answer)
	return answer, err
}

// BalancesReset is POST /journal/reset's answer: every pursuit whose balance starts again from now.
type BalancesReset struct {
	Reset []string `json:"reset"`
}

// ResetBalances starts the balance of each pursuit body names again from now, or of every one.
func (c *Client) ResetBalances(ctx context.Context, body map[string]any) (BalancesReset, error) {
	var answer BalancesReset
	err := c.Post(ctx, "/journal/reset", body, false, &answer)
	return answer, err
}
