package service

import (
	"context"
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
	Groups      []Lane   `json:"groups"`
	Problems    []string `json:"problems"`
}

// Happened reads the window that since and until bound. Either may be "",
// and the service starts a window with neither a week back.
func (c *Client) Happened(ctx context.Context, since, until string) (Happened, error) {
	var answer Happened
	err := c.Get(ctx, "/happened", HappenedQuery(since, until), &answer)
	return answer, err
}

// HappenedQuery is the query GET /happened takes, which `doit happened --json` sends too.
func HappenedQuery(since, until string) url.Values {
	query := url.Values{}
	if since != "" {
		query.Set("since", since)
	}
	if until != "" {
		query.Set("until", until)
	}
	return query
}
