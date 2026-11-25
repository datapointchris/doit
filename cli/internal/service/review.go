package service

import (
	"context"
	"encoding/json"
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
