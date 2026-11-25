package service

import (
	"context"
	"encoding/json"
	"net/url"
)

// Happened is GET /happened: what every record says happened over a window,
// one group per record asked.
type Happened struct {
	// Since and Until are the window's ends, with the service's offset. A
	// window asked with no start begins at the Unix epoch.
	Since string `json:"since"`
	Until string `json:"until"`
	// Days is how many of the service's local days the window touches.
	Days int `json:"days"`
	// Asked names every group, Unavailable each that could not be read, and
	// Partial each read in part.
	Asked       []string `json:"asked"`
	Unavailable []string `json:"unavailable"`
	Partial     []string `json:"partial"`
	// RecordedBy names each group whose record wrote who did each entry.
	RecordedBy []string `json:"recorded_by"`
	// UnknownActors counts the entries whose actor the service does not recognize.
	UnknownActors int      `json:"unknown_actors"`
	Groups        []Lane   `json:"groups"`
	Problems      []string `json:"problems"`
}

// Happened reads the window that since and until bound, and answers the body
// it came in too. Either end may be "", and the service picks the start of a
// window given neither end.
func (c *Client) Happened(ctx context.Context, since, until string) (json.RawMessage, Happened, error) {
	var answer Happened
	raw, err := c.Get(ctx, "/happened", happenedQuery(since, until), &answer)
	return raw, answer, err
}

// happenedQuery is the query GET /happened takes.
func happenedQuery(since, until string) url.Values {
	query := url.Values{}
	if since != "" {
		query.Set("since", since)
	}
	if until != "" {
		query.Set("until", until)
	}
	return query
}
