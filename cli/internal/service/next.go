package service

import (
	"context"
	"net/url"
)

// Next is GET /next: the draw standing for this window, or a fresh one, with
// each row its screens print. The service orders and prices every row, so the
// screens only lay them out.
type Next struct {
	DrawID string `json:"draw_id"`
	// OfferRows are the offered pursuits in the draw's order, then each other
	// active one, owed first.
	OfferRows []OfferRow `json:"offer_rows"`
	// ExplainRows are every active pursuit, owed first.
	ExplainRows []ExplainRow `json:"explain_rows"`
	// Context is today's events and near countdowns, one line each.
	Context []string `json:"context"`
	// ContextError is why the events could not be read, or null.
	ContextError string `json:"context_error"`
	// Pursuits is the register, which the names cache lists.
	Pursuits map[string]RegisterEntry `json:"pursuits"`
}

// OfferRow is one pursuit's row on `doit next`, each cell as it prints.
type OfferRow struct {
	Name    string `json:"name"`
	Offered bool   `json:"offered"`
	Weight  string `json:"weight"`
	Goal    string `json:"goal"`
	Tally   string `json:"tally"`
	Due     string `json:"due"`
	// Late is a pursuit past its due date by a day or more.
	Late bool `json:"late"`
	// Choices are what an offered pursuit could be, a line each.
	Choices []string `json:"choices"`
	// Failed is a resolve that failed or matched nothing, which its choice says.
	Failed bool `json:"failed"`
}

// ExplainRow is one pursuit's row on `doit next --explain`, each cell as it prints.
type ExplainRow struct {
	Name    string `json:"name"`
	Offered bool   `json:"offered"`
	Skipped bool   `json:"skipped"`
	Weight  string `json:"weight"`
	Goal    string `json:"goal"`
	Last    string `json:"last"`
	Due     string `json:"due"`
	Pick    string `json:"pick"`
	// Reason is the register's reason for the pursuit, or "".
	Reason string `json:"reason"`
}

// RegisterEntry is one pursuit as the register declares it, as far as the names cache reads it.
type RegisterEntry struct {
	Description any `json:"description,omitempty"`
}

// Next reads the draw. machine is the desk it is offered to, and reroll asks
// for a fresh draw though one stands.
func (c *Client) Next(ctx context.Context, machine string, reroll bool) (Next, error) {
	var answer Next
	err := c.Get(ctx, "/next", NextQuery(machine, reroll), &answer)
	return answer, err
}

// NextQuery is the query GET /next takes, which `doit next --json` sends too.
func NextQuery(machine string, reroll bool) url.Values {
	query := url.Values{"machine": {machine}}
	if reroll {
		query.Set("reroll", "true")
	}
	return query
}
