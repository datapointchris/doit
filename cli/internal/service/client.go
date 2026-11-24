// Package service makes every request the Go doit sends to doit's service. It
// is written by hand against the wire contract that the Python client keeps.
//
// A route's call is a method on Client that names its path and the Go type of
// its answer. The verb needs every field that type declares without omitempty.
// A Go release can reach a desk before the cluster rolls the matching service.
// So Get refuses an answer that lacks such a field, rather than drawing it
// empty, and names the field and the service's version.
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goclilogin"
	"golang.org/x/oauth2"

	"github.com/datapointchris/doit/cli/internal/login"
)

// Prefix is where every route sits, the OpenAPI document among them. The
// cluster's edge forwards nothing else to the service but its login metadata.
const Prefix = "/api/v1"

const userAgent = "doit-cli (+https://github.com/datapointchris/doit)"

// A view reads each app within its own bound, and a read here outlasts the
// longest of those. A slow app then costs the view one lane rather than the
// whole answer.
const (
	readTimeout    = 60 * time.Second
	connectTimeout = 10 * time.Second
)

// One read with its retries ends within budget. Each retried status is a proxy
// or a busy service answering before the request was handled.
const (
	budget       = 90 * time.Second
	attempts     = 3
	longestPause = 20 * time.Second
	versionWait  = 10 * time.Second
)

var retriedStatuses = []int{
	http.StatusTooManyRequests,
	http.StatusBadGateway,
	http.StatusServiceUnavailable,
	http.StatusGatewayTimeout,
}

// Client is one authenticated client of the service at base.
type Client struct {
	base   string
	where  string
	repair string
	http   *http.Client
	pause  func(context.Context, time.Duration)
}

// New is a client of the service at base, sending source's token. chosenBy says
// where base came from, and repair what fixes a refused token.
func New(base string, source oauth2.TokenSource, chosenBy, repair string) *Client {
	dialer := &net.Dialer{Timeout: connectTimeout}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = dialer.DialContext
	where := "the doit service at " + strings.TrimRight(base, "/")
	if chosenBy != "" {
		where += " (" + chosenBy + ")"
	}
	return &Client{
		base:   strings.TrimRight(base, "/"),
		where:  where,
		repair: repair,
		http: &http.Client{
			Transport: &oauth2.Transport{Source: source, Base: goclikit.TraceForwardingTransport(transport)},
			Timeout:   readTimeout,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
		pause: sleep,
	}
}

// Open is the service this machine's config file and login name.
func Open(ctx context.Context) (*Client, error) {
	deployment, err := login.Load(ctx)
	if err != nil {
		return nil, err
	}
	source, err := login.TokenSource(ctx, deployment, goclilogin.NewTokenStore(login.Config(deployment)))
	if err != nil {
		return nil, err
	}
	repair := "Run `doit auth login`."
	if deployment.HandsTokens() {
		repair = fmt.Sprintf("Check what token_command in %s hands out: %#q.", deployment.Path, deployment.TokenCommand.String())
	}
	return New(deployment.API.Value, source, "api in "+deployment.Path, repair), nil
}

// Refused is the service refusing a request, in the sentence it gave.
type Refused struct {
	Status  int
	Message string
}

func (r *Refused) Error() string { return r.Message }

// NoAnswer is no answer coming: the service was never reached, timed out, or
// was busy on every attempt. Run unchanged later, the same command can work.
type NoAnswer struct {
	Message string
}

func (n *NoAnswer) Error() string { return n.Message }

// MissingField is an answer that lacks a field which the verb needs.
type MissingField struct {
	Where   string
	Route   string
	Field   string
	Version string
	// VersionUnread is why the service's version could not be read, where it could not.
	VersionUnread error
}

func (m *MissingField) Error() string {
	version := "version " + m.Version
	if m.VersionUnread != nil {
		version = "its version unread: " + m.VersionUnread.Error()
	}
	return fmt.Sprintf("%s (%s) answered GET %s without %s, which this doit needs. A release of the service that serves it fixes this.",
		m.Where, version, m.Route, m.Field)
}

// NoAnswerExit is what a command exits with where no answer came, the one
// failure that the same command run later can outlast.
const NoAnswerExit = 4

// ExitCode is what a command exits with for err: NoAnswerExit where no answer came, else 1.
func ExitCode(err error) int {
	var none *NoAnswer
	if errors.As(err, &none) {
		return NoAnswerExit
	}
	return 1
}

// Get reads the route at path into answer, refusing an answer that lacks a
// field answer's type declares without omitempty.
func (c *Client) Get(ctx context.Context, path string, query url.Values, answer any) error {
	route := Prefix + path
	body, err := c.read(ctx, route, query, budget)
	if err != nil {
		return err
	}
	var parsed any
	if err := json.Unmarshal(body, &parsed); err != nil {
		return &Refused{Message: fmt.Sprintf("%s answered GET %s with a body that is not JSON", c.where, route)}
	}
	if field := missingFieldOf(answer, parsed); field != "" {
		refusal := &MissingField{Where: c.where, Route: route, Field: field}
		refusal.Version, refusal.VersionUnread = c.Version(ctx)
		return refusal
	}
	if err := json.Unmarshal(body, answer); err != nil {
		return &Refused{Message: fmt.Sprintf("%s answered GET %s in a shape this doit does not read: %v", c.where, route, err)}
	}
	return nil
}

// Version is the service's own, from its OpenAPI document's info.version.
func (c *Client) Version(ctx context.Context) (string, error) {
	body, err := c.read(ctx, Prefix+"/openapi.json", nil, versionWait)
	if err != nil {
		return "", err
	}
	var document struct {
		Info struct {
			Version string `json:"version"`
		} `json:"info"`
	}
	if err := json.Unmarshal(body, &document); err != nil || document.Info.Version == "" {
		return "", errors.New("its OpenAPI document names no info.version")
	}
	return document.Info.Version, nil
}

// read is one GET, tried again where a later attempt can be answered, with
// every attempt and pause ending within limit.
func (c *Client) read(ctx context.Context, route string, query url.Values, limit time.Duration) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()
	target := c.base + route
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	reason := "the budget ran out"
	attempt := 0
	for attempt < attempts && ctx.Err() == nil {
		attempt++
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, http.NoBody)
		if err != nil {
			return nil, err
		}
		request.Header.Set("User-Agent", userAgent)
		request.Header.Set("Accept", "application/json")
		response, err := c.http.Do(request)
		var wait time.Duration
		switch {
		case err != nil && goclilogin.IsSessionRejected(err):
			return nil, &Refused{Message: fmt.Sprintf("the identity provider no longer honors this machine's login. %s", c.repair)}
		case err != nil:
			reason = err.Error()
		case slices.Contains(retriedStatuses, response.StatusCode):
			reason = "HTTP " + strconv.Itoa(response.StatusCode)
			wait = retryAfter(response)
			_ = response.Body.Close()
		default:
			return c.answered(response, route)
		}
		if attempt < attempts {
			if wait == 0 {
				wait = jitter(attempt)
			}
			c.pause(ctx, wait)
		}
	}
	plural := "s"
	if attempt == 1 {
		plural = ""
	}
	return nil, &NoAnswer{Message: fmt.Sprintf("no answer from %s: %s, %d attempt%s", c.where, reason, attempt, plural)}
}

// answered is the body of an answer, or the service's refusal.
func (c *Client) answered(response *http.Response, route string) ([]byte, error) {
	defer func() { _ = response.Body.Close() }()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, &NoAnswer{Message: fmt.Sprintf("%s broke off its answer to GET %s: %v", c.where, route, err)}
	}
	status := response.StatusCode
	if status == http.StatusUnauthorized {
		return nil, &Refused{Status: status, Message: fmt.Sprintf("%s refused this machine's token. %s", c.where, c.repair)}
	}
	if status < 200 || status >= 300 {
		var said struct {
			Message string `json:"message"`
		}
		if json.Unmarshal(body, &said) != nil || said.Message == "" {
			said.Message = fmt.Sprintf("%s answered GET %s with HTTP %d", c.where, route, status)
		}
		return nil, &Refused{Status: status, Message: said.Message}
	}
	return body, nil
}

// retryAfter is the service's Retry-After in seconds, bounded, or 0 where it sent none.
func retryAfter(response *http.Response) time.Duration {
	seconds, err := strconv.ParseFloat(response.Header.Get("Retry-After"), 64)
	if err != nil || seconds < 0 {
		return 0
	}
	return min(time.Duration(seconds*float64(time.Second)), longestPause)
}

// jitter is a random fraction of 2^(attempt-1) seconds, bounded.
func jitter(attempt int) time.Duration {
	ceiling := min(time.Duration(1<<(attempt-1))*time.Second, longestPause)
	return time.Duration(rand.Int64N(int64(ceiling) + 1))
}

func sleep(ctx context.Context, wait time.Duration) {
	timer := time.NewTimer(wait)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}
