package service

import (
	"context"
	"encoding/json"
)

// IngestionDecisions is what POST /ingestion/items/place and /drop answer:
// ingestion's own answer, which its schemas/review-placement.schema.json
// holds.
type IngestionDecisions struct {
	Key       string              `json:"key"`
	Decisions []IngestionDecision `json:"decisions"`
}

// IngestionDecision is one decision ingestion recorded as the caller's.
type IngestionDecision struct {
	Gate        string  `json:"gate"`
	Verdict     string  `json:"verdict"`
	Destination *string `json:"destination,omitempty"`
	Lens        *string `json:"lens,omitempty"`
}

// IngestionDecided is a placement or a drop of one item, as sent.
type IngestionDecided struct {
	Key         string  `json:"key"`
	Destination string  `json:"destination,omitempty"`
	Cause       *string `json:"cause,omitempty"`
	Note        *string `json:"note,omitempty"`
}

// PlaceIngestionItem places the item on decided.Destination as this login's
// decision. An item ingestion does not hold is refused with 404, and a
// destination it does not declare or a cause it cannot read with 422.
func (c *Client) PlaceIngestionItem(ctx context.Context, decided IngestionDecided) (json.RawMessage, IngestionDecisions, error) {
	return c.decideIngestionItem(ctx, "/ingestion/items/place", decided)
}

// DropIngestionItem drops the item as this login's decision. decided names no
// destination.
func (c *Client) DropIngestionItem(ctx context.Context, decided IngestionDecided) (json.RawMessage, IngestionDecisions, error) {
	return c.decideIngestionItem(ctx, "/ingestion/items/drop", decided)
}

// decideIngestionItem sends decided as a repeatable write. ingestion's
// review-placement schema says the same body sent again answers the decisions
// the first made and appends none.
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
