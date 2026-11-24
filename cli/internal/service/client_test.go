package service

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"golang.org/x/oauth2"
)

type row struct {
	Key     string  `json:"key"`
	Minutes *int    `json:"minutes"`
	Note    string  `json:"note,omitempty"`
	Group   *string `json:"group"`
}

type panel struct {
	Rows []row `json:"rows"`
}

func TestAnAnswerMissingAFieldTheVerbNeedsIsRefusedNamingTheFieldAndTheServicesVersion(t *testing.T) {
	answers := map[string]string{
		"/api/v1/whole":        `{"rows": [{"key": "a", "minutes": 20, "group": null}]}`,
		"/api/v1/missing":      `{"rows": [{"key": "a", "minutes": 20, "group": "x"}, {"key": "b", "group": "x"}]}`,
		"/api/v1/openapi.json": `{"openapi": "3.1.0", "info": {"title": "doit-api", "version": "3.4.0"}}`,
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer t" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(answers[r.URL.Path]))
	}))
	defer server.Close()
	client := New(server.URL, oauth2.StaticTokenSource(&oauth2.Token{AccessToken: "t"}), "", "Run `doit auth login`.")

	var whole panel
	if err := client.Get(t.Context(), "/whole", nil, &whole); err != nil || len(whole.Rows) != 1 || whole.Rows[0].Group != nil {
		t.Fatalf("an answer with every field, one null and one left out by omitempty: %v, %+v", err, whole)
	}

	var partial panel
	err := client.Get(t.Context(), "/missing", nil, &partial)
	var refused *MissingField
	if !errors.As(err, &refused) || refused.Field != "rows[1].minutes" || refused.Version != "3.4.0" {
		t.Fatalf("got %v; want a refusal naming rows[1].minutes and version 3.4.0", err)
	}
	if !strings.Contains(err.Error(), "rows[1].minutes") || !strings.Contains(err.Error(), "version 3.4.0") || ExitCode(err) != 1 {
		t.Errorf("refusal %q exits %d; want it to name the field and the version, and exit 1", err, ExitCode(err))
	}
}
