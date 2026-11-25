package main

import (
	"errors"
	"net/http"

	"github.com/datapointchris/goclikit"

	"github.com/datapointchris/doit/cli/internal/service"
)

// refusedAsTyped turns the service's refusal of what was typed into a usage
// error, which exits 2. The service answers 404 for a name or an id that
// selects nothing, and 422 for a value it cannot read.
func refusedAsTyped(err error) error {
	var refused *service.Refused
	if errors.As(err, &refused) && (refused.Status == http.StatusNotFound || refused.Status == http.StatusUnprocessableEntity) {
		return goclikit.UsageError(err)
	}
	return err
}
