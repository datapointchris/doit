package service

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// Reading is one forecast as the service answers it, with the rows its screen prints.
type Reading struct {
	// Generated is when it was taken, as an ISO 8601 instant.
	Generated            string  `json:"generated"`
	Machine              string  `json:"machine"`
	BudgetMinutes        int     `json:"budget_minutes"`
	Replicates           int     `json:"replicates"`
	UnspentMinutesPerDay float64 `json:"unspent_minutes_per_day"`
	// Rows are each pursuit's, doing most over the month first.
	Rows []ReadingRow `json:"rows"`
	// Estimates says how many durations were measured and how many assumed.
	Estimates string `json:"estimates"`
}

// ReadingRow is one pursuit's row of a reading, each cell as it prints.
type ReadingRow struct {
	Name  string `json:"name"`
	Week  string `json:"week"`
	Month string `json:"month"`
	Hours string `json:"hours"`
	// Goal is null for a reading taken before goals were recorded.
	Goal   *string `json:"goal"`
	OfGoal string  `json:"of_goal"`
	// ShortOfGoal is the service judging the month short of its goal.
	ShortOfGoal bool   `json:"short_of_goal"`
	Estimate    string `json:"estimate"`
}

// Trend is GET /forecasts/trend: the matured readings graded at one horizon.
type Trend struct {
	Days int `json:"days"`
	// Readings is how many readings were old enough to grade.
	Readings int `json:"readings"`
	// Rows are each pursuit's verdict, furthest off first.
	Rows []TrendRow `json:"rows"`
}

// TrendRow is one verdict's row, each cell as it prints.
type TrendRow struct {
	Pursuit string `json:"pursuit"`
	Said    string `json:"said"`
	Did     string `json:"did"`
	Error   string `json:"error"`
	// Missed is a prediction off by a whole occasion or more.
	Missed bool `json:"missed"`
}

// forecastPath is the route of the reading a timestamp prefix names, or the newest where handle is "".
func forecastPath(handle string) string {
	if handle == "" {
		handle = "latest"
	}
	return "/forecasts/" + url.PathEscape(handle)
}

// Forecast reads the reading handle names, or the newest where it is "", and
// answers the body it came in too.
func (c *Client) Forecast(ctx context.Context, handle string) (json.RawMessage, Reading, error) {
	var answer Reading
	raw, err := c.Get(ctx, forecastPath(handle), nil, &answer)
	return raw, answer, err
}

// Forecasts reads every stored reading, oldest first, and answers the body it came in too.
func (c *Client) Forecasts(ctx context.Context) (json.RawMessage, []Reading, error) {
	var answer []Reading
	raw, err := c.Get(ctx, "/forecasts", nil, &answer)
	return raw, answer, err
}

// Trend grades the readings whose days-long window has closed, at the
// service's own horizon where days is nil, and answers the body it came in too.
func (c *Client) Trend(ctx context.Context, days *int) (json.RawMessage, Trend, error) {
	query := url.Values{}
	if days != nil {
		query.Set("days", strconv.Itoa(*days))
	}
	var answer Trend
	raw, err := c.Get(ctx, "/forecasts/trend", query, &answer)
	return raw, answer, err
}

// TakeForecast takes a forecast at budget minutes a day, or at the register's
// budget where budget is nil, and stores it. It answers the reading as the
// service sent it, for a --json that prints it whole, beside the reading read.
// Where the answer cannot be read, the reading is still stored, so the body
// comes back beside the error, and the error says so.
func (c *Client) TakeForecast(ctx context.Context, budget *int) (json.RawMessage, Reading, error) {
	body := map[string]any{"budget_minutes": budget}
	var raw json.RawMessage
	if err := c.Post(ctx, "/forecasts", body, false, &raw); err != nil {
		return nil, Reading{}, err
	}
	var reading Reading
	if err := c.decode(ctx, http.MethodPost, Prefix+"/forecasts", raw, &reading); err != nil {
		return raw, Reading{}, fmt.Errorf("%w The reading was stored, so `doit forecast show` reads it back, and running this again stores a second one", err)
	}
	return raw, reading, nil
}
