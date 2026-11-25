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
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"slices"
	"strings"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goclilogin"
	"golang.org/x/oauth2"

	"github.com/datapointchris/doit/cli/internal/login"
)

// Prefix is where every route sits, the OpenAPI document among them.
// deploy/api.yaml names the paths the cluster routes to the service.
const Prefix = "/api/v1"

const userAgent = "doit-cli (+https://github.com/datapointchris/doit)"

// The bounds of one attempt. A view answers once each app it reads has
// answered or timed out, so a source's timeout past answerTimeout is cut off
// here.
const (
	answerTimeout  = 60 * time.Second
	connectTimeout = 10 * time.Second
)

// One read with its retries ends within budget, and a read of the version
// within versionWait.
const (
	budget      = 90 * time.Second
	versionWait = 10 * time.Second
)

// retriedStatuses are a proxy or a busy service answering before the request
// was handled.
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
}

// New is a client of the service at base, sending source's token. chosenBy says
// where base came from, and repair what fixes a refused token.
func New(base string, source oauth2.TokenSource, chosenBy, repair string) *Client {
	dialer := &net.Dialer{Timeout: connectTimeout}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = dialer.DialContext
	transport.ResponseHeaderTimeout = answerTimeout
	where := "the doit service at " + strings.TrimRight(base, "/")
	if chosenBy != "" {
		where += " (" + chosenBy + ")"
	}
	return &Client{
		base:   strings.TrimRight(base, "/"),
		where:  where,
		repair: repair,
		http: &http.Client{
			Transport: retryingTransport{
				next:  &oauth2.Transport{Source: source, Base: goclikit.TraceForwardingTransport(transport)},
				pause: randomPause,
			},
			CheckRedirect: func(*http.Request, []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
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
	Where string
	// Method is the request's, GET where it is "".
	Method  string
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
	method := m.Method
	if method == "" {
		method = http.MethodGet
	}
	return fmt.Sprintf("%s (%s) answered %s %s without %s, which this doit needs. A release of the service that serves it fixes this.",
		m.Where, version, method, m.Route, m.Field)
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
// field answer's type declares without omitempty. It also answers the body as
// it came, for a --json that prints it whole, fields this doit does not read
// included.
func (c *Client) Get(ctx context.Context, path string, query url.Values, answer any) (json.RawMessage, error) {
	route := Prefix + path
	body, err := c.read(ctx, route, query, budget)
	if err != nil {
		return nil, err
	}
	if err := c.decode(ctx, http.MethodGet, route, body, answer); err != nil {
		return nil, err
	}
	return body, nil
}

// decode reads the body that method on route answered into answer, refusing
// one that lacks a field answer's type declares without omitempty.
func (c *Client) decode(ctx context.Context, method, route string, body []byte, answer any) error {
	var parsed any
	if err := json.Unmarshal(body, &parsed); err != nil {
		return &Refused{Message: fmt.Sprintf("%s answered %s %s with a body that is not JSON", c.where, method, route)}
	}
	if field := missingFieldOf(answer, parsed); field != "" {
		refusal := &MissingField{Where: c.where, Method: method, Route: route, Field: field}
		refusal.Version, refusal.VersionUnread = c.Version(ctx)
		return refusal
	}
	if err := json.Unmarshal(body, answer); err != nil {
		return &Refused{Message: fmt.Sprintf("%s answered %s %s in a shape this doit does not read: %v", c.where, method, route, err)}
	}
	return nil
}

// Post sends body to the route at path and reads the answer into answer, which
// may be nil. The service stores a repeatable write once by the key in its
// body, so it is sent again after a timeout, a reset or a busy answer.
// Any other write is sent again only where it never reached the service.
func (c *Client) Post(ctx context.Context, path string, body any, repeatable bool, answer any) error {
	route := Prefix + path
	encoded, err := json.Marshal(body)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	if repeatable {
		ctx = context.WithValue(ctx, repeatableWrite{}, true)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+route, bytes.NewReader(encoded))
	if err != nil {
		return err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(request)
	switch {
	case err != nil && goclilogin.IsSessionRejected(err):
		return &Refused{Message: fmt.Sprintf("the identity provider no longer honors this machine's login. %s", c.repair)}
	case err != nil && repeatable:
		return &NoAnswer{Message: fmt.Sprintf("no answer from %s: %v", c.where, err)}
	case err != nil:
		return &NoAnswer{Message: fmt.Sprintf("%s did not answer POST %s (%v). It may have been recorded, so check before sending it again.", c.where, route, err)}
	case slices.Contains(retriedStatuses, response.StatusCode):
		_ = response.Body.Close()
		return &NoAnswer{Message: fmt.Sprintf("no answer from %s: HTTP %d", c.where, response.StatusCode)}
	}
	return c.decoded(ctx, http.MethodPost, route, response, answer)
}

// Put replaces what the route at path holds with body and reads the answer
// into answer, which may be nil. A put states the whole of what it sets, so
// it is sent again after a timeout, a reset or a busy answer.
func (c *Client) Put(ctx context.Context, path string, body, answer any) error {
	return c.send(ctx, http.MethodPut, path, body, answer)
}

// Delete removes what the route at path names and reads the answer into
// answer. A route that names what it removes removes nothing more when it is
// sent again.
func (c *Client) Delete(ctx context.Context, path string, answer any) error {
	return c.send(ctx, http.MethodDelete, path, nil, answer)
}

// send is one repeatable write of method to the route at path.
func (c *Client) send(ctx context.Context, method, path string, body, answer any) error {
	route := Prefix + path
	ctx, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	ctx = context.WithValue(ctx, repeatableWrite{}, true)
	reader := io.Reader(http.NoBody)
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, c.base+route, reader)
	if err != nil {
		return err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("Accept", "application/json")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(request)
	switch {
	case err != nil && goclilogin.IsSessionRejected(err):
		return &Refused{Message: fmt.Sprintf("the identity provider no longer honors this machine's login. %s", c.repair)}
	case err != nil:
		return &NoAnswer{Message: fmt.Sprintf("no answer from %s: %v", c.where, err)}
	case slices.Contains(retriedStatuses, response.StatusCode):
		_ = response.Body.Close()
		return &NoAnswer{Message: fmt.Sprintf("no answer from %s: HTTP %d", c.where, response.StatusCode)}
	}
	return c.decoded(ctx, method, route, response, answer)
}

// decoded reads the answer to method on route into answer, where answer is not nil.
func (c *Client) decoded(ctx context.Context, method, route string, response *http.Response, answer any) error {
	body, err := c.answered(response, method+" "+route)
	if err != nil || answer == nil {
		return err
	}
	return c.decode(ctx, method, route, body, answer)
}

// LaterSendCanStore is whether the same write sent later could be stored: no
// answer came, the login was refused, or the service broke. A refusal of the
// write itself is refused again however often it is sent, and an answer
// lacking a field came after the write was stored.
func LaterSendCanStore(err error) bool {
	var none *NoAnswer
	if errors.As(err, &none) || errors.Is(err, login.ErrNeedsLogin) || errors.Is(err, goclilogin.ErrNotConfigured) {
		return true
	}
	var missing *MissingField
	if errors.As(err, &missing) {
		return false
	}
	var refused *Refused
	if errors.As(err, &refused) {
		return refused.Status == 0 || refused.Status == http.StatusUnauthorized || refused.Status >= 500
	}
	return true
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

// read is one GET through the retrying transport, with every attempt and
// pause ending within limit.
func (c *Client) read(ctx context.Context, route string, query url.Values, limit time.Duration) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, limit)
	defer cancel()
	target := c.base + route
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, http.NoBody)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("Accept", "application/json")
	response, err := c.http.Do(request)
	switch {
	case err != nil && goclilogin.IsSessionRejected(err):
		return nil, &Refused{Message: fmt.Sprintf("the identity provider no longer honors this machine's login. %s", c.repair)}
	case err != nil:
		return nil, &NoAnswer{Message: fmt.Sprintf("no answer from %s: %v", c.where, err)}
	case slices.Contains(retriedStatuses, response.StatusCode):
		_ = response.Body.Close()
		return nil, &NoAnswer{Message: fmt.Sprintf("no answer from %s: HTTP %d on each of %d attempts", c.where, response.StatusCode, requestAttempts)}
	}
	return c.answered(response, "GET "+route)
}

// answered is the body of an answer, or the service's refusal.
func (c *Client) answered(response *http.Response, request string) ([]byte, error) {
	defer func() { _ = response.Body.Close() }()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		return nil, &NoAnswer{Message: fmt.Sprintf("%s broke off its answer to %s: %v", c.where, request, err)}
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
			said.Message = fmt.Sprintf("%s answered %s with HTTP %d", c.where, request, status)
		}
		return nil, &Refused{Status: status, Message: said.Message}
	}
	return body, nil
}
