package service

import (
	"context"
	"encoding/json"
	"net/url"
	"strconv"
)

// Review is GET /review: every register entry with its derived status, most
// urgent first, and each block's heading.
type Review struct {
	Entries []ReviewEntry `json:"entries"`
	// Stranded are entries with recorded done dates that the register does not hold.
	Stranded []string `json:"stranded"`
	Blocks   []Block  `json:"blocks"`
}

// ReviewEntry is one register entry.
type ReviewEntry struct {
	ID      string  `json:"id"`
	Block   string  `json:"block"`
	Cadence string  `json:"cadence"`
	Desc    string  `json:"desc"`
	Command string  `json:"command"`
	Show    string  `json:"show"`
	Last    *string `json:"last"`
	// Due is never done, or on or past its due date.
	Due bool `json:"due"`
	// Status is the label every screen prints, such as `overdue 3d`.
	Status string `json:"status"`
}

// Block is one of the register's blocks, in the order the browse views print them.
type Block struct {
	Name  string `json:"name"`
	Title string `json:"title"`
}

// Review reads the register.
func (c *Client) Review(ctx context.Context) (json.RawMessage, Review, error) {
	var answer Review
	raw, err := c.Get(ctx, "/review", nil, &answer)
	return raw, answer, err
}

// Nudge is GET /review/nudge: whether the shell nudge shows now, and what it shows.
type Nudge struct {
	IntervalMinutes int           `json:"interval_minutes"`
	Show            bool          `json:"show"`
	Due             []ReviewEntry `json:"due"`
	DueTotal        int           `json:"due_total"`
}

// Nudge reads the shell nudge.
func (c *Client) Nudge(ctx context.Context) (Nudge, error) {
	var answer Nudge
	_, err := c.Get(ctx, "/review/nudge", nil, &answer)
	return answer, err
}

// NudgeHeld is PUT /review/nudge's answer: the interval, as the service holds it.
type NudgeHeld struct {
	IntervalMinutes int `json:"interval_minutes"`
}

// SetNudge sets what body names of the shell nudge: `every`, or `last_nudged_at`.
func (c *Client) SetNudge(ctx context.Context, body map[string]string) (NudgeHeld, error) {
	var held NudgeHeld
	err := c.Put(ctx, "/review/nudge", body, &held)
	return held, err
}

// ReviewStatus is an entry's derived status, as the service answers it.
type ReviewStatus struct {
	CadenceDays *float64 `json:"cadence_days"`
	Last        *string  `json:"last"`
	// Label is the status as every screen prints it, such as `overdue 3d`.
	Label string `json:"status"`
}

// ReviewDone is the answer to a completion or to one taken back.
type ReviewDone struct {
	DoneOn string        `json:"done_on"`
	Status *ReviewStatus `json:"status"`
}

// DueAgain is how many days until the entry comes due again, as `7d`, or "" where it has no cadence.
func (d ReviewDone) DueAgain() string {
	if d.Status == nil || d.Status.CadenceDays == nil || *d.Status.CadenceDays == 0 {
		return ""
	}
	return strconv.FormatFloat(*d.Status.CadenceDays, 'g', -1, 64) + "d"
}

// completionsPath is the route of the completions that the entry called id holds.
func completionsPath(id string) string { return "/review/" + url.PathEscape(id) + "/completions" }

// CompleteReview records the entry called id done on day, the desk's own day.
// The service keeps one completion a day for an entry, so a resend records
// nothing twice. An id the register does not hold is refused with 404.
func (c *Client) CompleteReview(ctx context.Context, id, day string) (ReviewDone, error) {
	var answer ReviewDone
	err := c.Post(ctx, completionsPath(id), map[string]string{"day": day}, true, &answer)
	return answer, err
}

// ReviewCompletion is one day an entry was done.
type ReviewCompletion struct {
	DoneOn string `json:"done_on"`
}

// ReviewCompletions reads every completion the entry called id holds, oldest day first.
func (c *Client) ReviewCompletions(ctx context.Context, id string) ([]ReviewCompletion, error) {
	var answer []ReviewCompletion
	_, err := c.Get(ctx, completionsPath(id), nil, &answer)
	return answer, err
}

// UndoReviewCompletion takes back the entry's completion on day. Naming the
// day means a resend after a lost answer cannot take back the one before it.
func (c *Client) UndoReviewCompletion(ctx context.Context, id, day string) (ReviewDone, error) {
	var answer ReviewDone
	err := c.Delete(ctx, completionsPath(id)+"/"+url.PathEscape(day), &answer)
	return answer, err
}
