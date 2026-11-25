// Package review records a review register entry done, as `doit review done`
// and the desk nudge watch's pick each do.
package review

import (
	"context"
	"net/url"
	"strconv"

	"github.com/datapointchris/doit/cli/internal/service"
)

// Status is an entry's derived status, as the service answers it.
type Status struct {
	CadenceDays *float64 `json:"cadence_days"`
	Last        *string  `json:"last"`
	// Label is the status as every screen prints it, such as `overdue 3d`.
	Label string `json:"status"`
}

// Done is the service's answer to a completion or to one taken back.
type Done struct {
	DoneOn string  `json:"done_on"`
	Status *Status `json:"status"`
}

// MarkDone records ref done on day, the desk's own day. The service keeps one
// completion a day for an entry, so a resend records nothing twice.
func MarkDone(ctx context.Context, client *service.Client, ref, day string) (Done, error) {
	var done Done
	err := client.Post(ctx, "/review/"+url.PathEscape(ref)+"/completions", map[string]string{"day": day}, true, &done)
	return done, err
}

// DueAgain is how many days until the entry comes due again, as `7d`, or "" where it has no cadence.
func (d Done) DueAgain() string {
	if d.Status == nil || d.Status.CadenceDays == nil || *d.Status.CadenceDays == 0 {
		return ""
	}
	return strconv.FormatFloat(*d.Status.CadenceDays, 'g', -1, 64) + "d"
}
