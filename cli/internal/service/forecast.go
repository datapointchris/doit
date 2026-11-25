package service

import (
	"context"
	"encoding/json"
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
	// ShortOfGoal is a month reaching under 90% of its goal.
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

// ForecastPath is the route of the reading a timestamp prefix names, or the newest where handle is "".
func ForecastPath(handle string) string {
	if handle == "" {
		handle = "latest"
	}
	return "/forecasts/" + url.PathEscape(handle)
}

// Forecast reads the reading handle names, or the newest where it is "".
func (c *Client) Forecast(ctx context.Context, handle string) (Reading, error) {
	var answer Reading
	err := c.Get(ctx, ForecastPath(handle), nil, &answer)
	return answer, err
}

// Forecasts reads every stored reading, oldest first.
func (c *Client) Forecasts(ctx context.Context) ([]Reading, error) {
	var answer []Reading
	err := c.Get(ctx, "/forecasts", nil, &answer)
	return answer, err
}

// TrendQuery is the query GET /forecasts/trend takes.
func TrendQuery(days int) url.Values {
	return url.Values{"days": {strconv.Itoa(days)}}
}

// Trend grades the readings whose days-long window has closed.
func (c *Client) Trend(ctx context.Context, days int) (Trend, error) {
	var answer Trend
	err := c.Get(ctx, "/forecasts/trend", TrendQuery(days), &answer)
	return answer, err
}

// TakeForecast takes a forecast at budget minutes a day, or at the register's
// budget where budget is 0, and stores it. It answers the reading as the
// service sent it, for a --json that prints it whole, beside the reading read.
func (c *Client) TakeForecast(ctx context.Context, budget int) (json.RawMessage, Reading, error) {
	body := map[string]any{"budget_minutes": nil}
	if budget > 0 {
		body["budget_minutes"] = budget
	}
	var raw json.RawMessage
	if err := c.Post(ctx, "/forecasts", body, false, &raw); err != nil {
		return nil, Reading{}, err
	}
	var reading Reading
	if err := c.decode(ctx, http.MethodPost, Prefix+"/forecasts", raw, &reading); err != nil {
		return nil, Reading{}, err
	}
	return raw, reading, nil
}
