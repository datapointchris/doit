package service

import "context"

// Today is GET /today: what the day still owes, above what it has had, in the
// lane contract's shape. It declares only the fields the screen reads.
type Today struct {
	// GeneratedAt is when the service read the day, with its offset. The
	// screen's date is the date there.
	GeneratedAt string    `json:"generated_at"`
	Done        []Lane    `json:"done"`
	Due         []DueLane `json:"due"`
	// Quota is null where the service drew no figures.
	Quota    *Quota   `json:"quota"`
	Problems []string `json:"problems"`
}

// Lane is one group of a list: ranked rows, or a grid of finished cells.
type Lane struct {
	Name string `json:"name"`
	// Status is "unavailable" where the group could not be read, and Reason says why.
	Status string `json:"status"`
	// Reason is null where the group read whole, and says what it could not
	// read where it read in part.
	Reason string     `json:"reason"`
	Rows   []LaneRow  `json:"rows"`
	Grid   []GridCell `json:"grid"`
	// Total is how many rows the group holds before any cut.
	Total int      `json:"total"`
	Hints []string `json:"hints"`
}

// Available is whether the group was read.
func (l Lane) Available() bool { return l.Status != "unavailable" }

// DueLane is a group of the due list, with how many of its rows the screen lists.
type DueLane struct {
	Lane
	RowCap int `json:"row_cap"`
}

// LaneRow is one thing to act on.
type LaneRow struct {
	Label string `json:"label"`
	Text  string `json:"text"`
	Note  string `json:"note"`
	// Urgency is none, due or overdue, and colors the note.
	Urgency string `json:"urgency"`
	// Handle is the command that acts on the row.
	Handle string `json:"handle"`
}

// GridCell is one thing done.
type GridCell struct {
	Text string `json:"text"`
	// DoneAt is the instant it was done, or "" where its record kept only the day.
	DoneAt string `json:"done_at"`
	// ActorKind is who did it: by hand, session, unattended or unknown, or ""
	// where its record names nobody.
	ActorKind string `json:"actor_kind"`
}

// Quota is the figures above both lists.
type Quota struct {
	// Minutes and Checkoffs are null where no pursuits were read.
	Minutes    *QuotaUnit       `json:"minutes"`
	Checkoffs  *QuotaUnit       `json:"checkoffs"`
	CatchUp    []QuotaCatchUp   `json:"catch_up"`
	Checklists []QuotaChecklist `json:"checklists"`
	// Unavailable says why the pursuits could not be read, or is "".
	Unavailable string `json:"unavailable"`
}

// QuotaUnit is the pursuits counted in one unit, summed.
type QuotaUnit struct {
	Need    float64 `json:"need"`
	Done    float64 `json:"done"`
	Behind  float64 `json:"behind"`
	AskedOf int     `json:"asked_of"`
}

// QuotaCatchUp is what more a day would clear the backlog over one timescale.
type QuotaCatchUp struct {
	Label           string  `json:"label"`
	MinutesPerDay   float64 `json:"minutes_per_day"`
	CheckoffsPerDay float64 `json:"checkoffs_per_day"`
	Shown           bool    `json:"shown"`
}

// QuotaChecklist is a checklist beside the figures, or why it could not be read.
type QuotaChecklist struct {
	Name        string `json:"name"`
	Done        int    `json:"done"`
	Total       int    `json:"total"`
	Unavailable string `json:"unavailable"`
}

// Today reads the day.
func (c *Client) Today(ctx context.Context) (Today, error) {
	var answer Today
	err := c.Get(ctx, "/today", nil, &answer)
	return answer, err
}
