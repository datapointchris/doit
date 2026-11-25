package service

import (
	"context"
	"encoding/json"
)

// Today is GET /today: what the day still owes, above what it has had, in the
// lane contract's shape. The screens read every field it declares.
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

// DueLane is a group of the due list, with how many of its rows the screen lists.
type DueLane struct {
	Lane
	RowCap int `json:"row_cap"`
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

// Today reads the day, and answers the body it came in too.
func (c *Client) Today(ctx context.Context) (json.RawMessage, Today, error) {
	var answer Today
	raw, err := c.Get(ctx, "/today", nil, &answer)
	return raw, answer, err
}
