package service

import (
	"context"
	"encoding/json"
	"net/url"
)

// PursuitList is GET /pursuits: every pursuit and the state behind it, with
// each row that `doit pursuits list` prints.
type PursuitList struct {
	Pursuits map[string]RegisterEntry `json:"pursuits"`
	Rows     []PursuitRow             `json:"rows"`
}

// PursuitRow is one pursuit's row on `doit pursuits list`, heaviest first, each cell as it prints.
type PursuitRow struct {
	Name string `json:"name"`
	// Paced is a pursuit that declares a pace. One that does not is tracked, and has no weight or due date.
	Paced  bool   `json:"paced"`
	Weight string `json:"weight"`
	Goal   string `json:"goal"`
	Last   string `json:"last"`
	Due    string `json:"due"`
	// Late is a pursuit past its due date by a day or more.
	Late bool `json:"late"`
	// Standing is why the pursuit sits out of the draw, such as `paused`, or "".
	Standing string `json:"standing"`
	Reason   string `json:"reason"`
}

// PursuitList reads every pursuit, and answers the body it came in too.
func (c *Client) PursuitList(ctx context.Context) (json.RawMessage, PursuitList, error) {
	var answer PursuitList
	raw, err := c.Get(ctx, "/pursuits", nil, &answer)
	return raw, answer, err
}

// Offers is GET /pursuits/offers: how often each pursuit was offered, and the
// recent counts of a pursuit that the register does not hold.
type Offers struct {
	// Recent is each machine's counts over Window, by pursuit.
	Recent   map[string]map[string]int `json:"recent"`
	Stranded []string                  `json:"stranded"`
	// Window is how far back Recent reaches, such as `90d`.
	Window string `json:"window"`
}

// Offers reads the offer counts.
func (c *Client) Offers(ctx context.Context) (Offers, error) {
	var answer Offers
	_, err := c.Get(ctx, "/pursuits/offers", nil, &answer)
	return answer, err
}

// Register is GET /pursuits/register: the pursuits as declared and what the
// standing draw offers, read with no app asked.
type Register struct {
	Pursuits map[string]RegisterEntry `json:"pursuits"`
	// Active are the pursuits in the draw.
	Active  []string `json:"active"`
	Offered []string `json:"offered"`
	// PromptRows are each pursuit as a prompt lists it, heaviest first.
	PromptRows []PromptRow `json:"prompt_rows"`
}

// PromptRow is one pursuit as a prompt for one lists it.
type PromptRow struct {
	Name    string `json:"name"`
	Summary string `json:"summary"`
}

// Register reads the register.
func (c *Client) Register(ctx context.Context) (Register, error) {
	var answer Register
	_, err := c.Get(ctx, "/pursuits/register", nil, &answer)
	return answer, err
}

// Drift is GET /pursuits/drift: what each goal asked for over a window, and
// what got done, each row also as it prints.
type Drift struct {
	Since       *string    `json:"since"`
	Until       string     `json:"until"`
	PrintedRows []DriftRow `json:"printed_rows"`
	// Rows are the same rows as numbers, which say whether anything was done.
	Rows []DriftAmount `json:"rows"`
}

// DriftAmount is how much of a pursuit got done over the window, in its own unit.
type DriftAmount struct {
	Done float64 `json:"done"`
}

// DriftRow is one pursuit's row on `doit pursuits drift`, each cell as it prints.
type DriftRow struct {
	Pursuit string `json:"pursuit"`
	Goal    string `json:"goal"`
	Since   string `json:"since"`
	Asked   string `json:"asked"`
	Done    string `json:"done"`
	Due     string `json:"due"`
	Offered string `json:"offered"`
	Passed  string `json:"passed"`
	Reason  string `json:"reason"`
}

// Drift reads drift over the window since and until name, each "" where not given.
func (c *Client) Drift(ctx context.Context, since, until string) (json.RawMessage, Drift, error) {
	var answer Drift
	raw, err := c.Get(ctx, "/pursuits/drift", window(since, until), &answer)
	return raw, answer, err
}

// window is the query that names a window's ends, leaving out each not given.
func window(since, until string) url.Values {
	query := url.Values{}
	if since != "" {
		query.Set("since", since)
	}
	if until != "" {
		query.Set("until", until)
	}
	return query
}

// Dormant is GET /pursuits/dormant, as schemas/pursuits-dormant.schema.json describes it.
type Dormant struct {
	Dormant []DormantRow `json:"dormant"`
}

// DormantRow is one pursuit gone quiet.
type DormantRow struct {
	Pursuit string  `json:"pursuit"`
	Goal    string  `json:"goal"`
	Last    string  `json:"last"`
	Reason  *string `json:"reason"`
}

// Dormant reads the pursuits gone quiet.
func (c *Client) Dormant(ctx context.Context) (json.RawMessage, Dormant, error) {
	var answer Dormant
	raw, err := c.Get(ctx, "/pursuits/dormant", nil, &answer)
	return raw, answer, err
}

// Evidence is GET /pursuits/evidence: what each app says was last done, asked now.
type Evidence struct {
	Rows []EvidenceRow `json:"rows"`
}

// EvidenceRow is one watched pursuit's row on `doit pursuits evidence`.
type EvidenceRow struct {
	Pursuit string `json:"pursuit"`
	// Kind is why the row reads as it does: undeclared, failed, observed or unseen.
	Kind string `json:"kind"`
	Text string `json:"text"`
	// At is when an observed pursuit was last done, which Text counts from.
	At *string `json:"at"`
}

// Evidence asks every app now.
func (c *Client) Evidence(ctx context.Context) (json.RawMessage, Evidence, error) {
	var answer Evidence
	raw, err := c.Get(ctx, "/pursuits/evidence", nil, &answer)
	return raw, answer, err
}
