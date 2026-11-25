package service

import (
	"context"
	"encoding/json"
)

// IngestionDecisions is what POST /ingestion/items/place and /drop answer:
// ingestion's own answer, which its schemas/review-placement.schema.json
// holds. Decisions are the side decision, then the read decision where a
// placement put the item on a side.
type IngestionDecisions struct {
	Key       string              `json:"key"`
	Decisions []IngestionDecision `json:"decisions"`
}

// IngestionDecision is one decision ingestion recorded as the caller's.
type IngestionDecision struct {
	Gate    string `json:"gate"`
	Verdict string `json:"verdict"`
	// Cause is what the caller named as deciding it, or unstated.
	Cause       string  `json:"cause"`
	Destination *string `json:"destination,omitempty"`
	// Lens is how a model reads the item, on a read decision whose verdict is model.
	Lens *string `json:"lens,omitempty"`
}

// IngestionDecided is a placement or a drop of one item, as sent.
type IngestionDecided struct {
	Key         string  `json:"key"`
	Destination string  `json:"destination,omitempty"`
	Cause       *string `json:"cause,omitempty"`
	Note        *string `json:"note,omitempty"`
}

// PlaceIngestionItem places the item on decided.Destination as this login's
// decision. ingestion records the same body sent again once, so a resend is
// safe. An item it does not hold is refused with 404, and a destination it
// does not declare or a cause it cannot read with 422.
func (c *Client) PlaceIngestionItem(ctx context.Context, decided IngestionDecided) (json.RawMessage, IngestionDecisions, error) {
	return c.decideIngestionItem(ctx, "/ingestion/items/place", decided)
}

// DropIngestionItem drops the item, which keeps its row in ingestion, as this
// login's decision. decided names no destination.
func (c *Client) DropIngestionItem(ctx context.Context, decided IngestionDecided) (json.RawMessage, IngestionDecisions, error) {
	return c.decideIngestionItem(ctx, "/ingestion/items/drop", decided)
}

func (c *Client) decideIngestionItem(ctx context.Context, path string, decided IngestionDecided) (json.RawMessage, IngestionDecisions, error) {
	var raw json.RawMessage
	if err := c.Post(ctx, path, decided, true, &raw); err != nil {
		return nil, IngestionDecisions{}, err
	}
	var answer IngestionDecisions
	if err := c.decode(ctx, "POST", Prefix+path, raw, &answer); err != nil {
		return nil, IngestionDecisions{}, err
	}
	return raw, answer, nil
}
