package service

import (
	"context"
	"encoding/json"
)

// Sources is GET /sources: every configured source and whether its read answers.
type Sources struct {
	Answers []SourceAnswer `json:"answers"`
	// Problems are what the sources document says that the service could not read.
	Problems []string `json:"problems"`
}

// SourceAnswer is one source's read.
type SourceAnswer struct {
	ID string `json:"id"`
	// Block is outstanding or happened.
	Block    string `json:"block"`
	Answered bool   `json:"answered"`
	Reason   string `json:"reason"`
	Lanes    int    `json:"lanes"`
	Via      string `json:"via"`
}

// Sources reads every configured source, and answers its answers as they came too.
func (c *Client) Sources(ctx context.Context) (json.RawMessage, Sources, error) {
	var answer Sources
	raw, err := c.Get(ctx, "/sources", nil, &answer)
	if err != nil {
		return nil, answer, err
	}
	var whole struct {
		Answers json.RawMessage `json:"answers"`
	}
	err = json.Unmarshal(raw, &whole)
	return whole.Answers, answer, err
}
