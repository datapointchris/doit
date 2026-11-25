package service

import (
	"context"
	"encoding/json"
	"net/url"
)

// Dashboard is GET /dashboard: one lane for each answer of a configured
// source, alert lanes first, then in the sources document's order, with
// doit's own maintenance lane last.
type Dashboard struct {
	GeneratedAt string          `json:"generated_at"`
	Lanes       []DashboardLane `json:"lanes"`
	// LaneNames names every lane the round built, before a lane query narrowed it.
	LaneNames []string `json:"lane_names"`
	Problems  []string `json:"problems"`
	// Configured counts the sources declared in the sources document.
	Configured int `json:"configured"`
	// PursuitStanding is the line naming the pursuits behind, or null where none is.
	PursuitStanding *PursuitStanding `json:"pursuit_standing"`
}

// DashboardLane is a lane with how many of its rows the screen lists, and
// whether the screen leaves it out.
type DashboardLane struct {
	Lane
	RowCap int `json:"row_cap"`
	// Quiet is a lane that answered with nothing open and asks not to be drawn then.
	Quiet bool `json:"quiet"`
}

// PursuitStanding is the dashboard's last line.
type PursuitStanding struct {
	Text string `json:"text"`
	// Unavailable is a line saying why the pursuits could not be read.
	Unavailable bool `json:"unavailable"`
}

// Dashboard reads every lane, or only those named in lanes, each then
// listing more of its rows. It answers the body it came in too.
func (c *Client) Dashboard(ctx context.Context, lanes []string) (json.RawMessage, Dashboard, error) {
	var answer Dashboard
	raw, err := c.Get(ctx, "/dashboard", dashboardQuery(lanes), &answer)
	return raw, answer, err
}

// dashboardQuery is the query GET /dashboard takes.
func dashboardQuery(lanes []string) url.Values {
	if len(lanes) == 0 {
		return nil
	}
	return url.Values{"lane": lanes}
}
