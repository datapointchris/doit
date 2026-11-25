// Package panelpage serves the desk nudge's panel page on 127.0.0.1, behind a
// Host check and a cookie that a one-use code buys. Any page open in a browser
// can send a request to 127.0.0.1, so a request is answered only where its Host
// names this listener, which stops DNS rebinding, and where it carries the
// cookie, which a cross-site request never does. Jupyter trades its token for a
// cookie the same way.
package panelpage

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"embed"
	"encoding/base64"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

// dist is the page that cli/web builds. A checkout holds only a placeholder
// index.html, which the release's build writes over before go build embeds it.
//
//go:embed dist
var dist embed.FS

const (
	// CodeLifetime is how long a minted code opens the page. The window loads
	// its URL within it, and a code seen in an argv afterwards opens nothing.
	CodeLifetime = 10 * time.Second
	// sessionsKept bounds how many cookies admit a request, the newest kept. Each open buys one.
	sessionsKept = 32
	cookieName   = "doit_panel"
	// contentPolicy admits only what the page's own origin serves.
	contentPolicy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
		"connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

// Page serves the built page at / and the watch's routes under /api/.
type Page struct {
	api    http.Handler
	files  http.Handler
	now    func() time.Time
	server *http.Server
	host   string

	mu       sync.Mutex
	codes    map[string]time.Time
	sessions []string
}

// New is a page over api, the watch's routes, on the clock now.
func New(api http.Handler, now func() time.Time) *Page {
	built, err := fs.Sub(dist, "dist")
	if err != nil {
		panic(err)
	}
	return &Page{api: api, files: http.FileServerFS(built), now: now, codes: map[string]time.Time{}}
}

// Listen serves the page on a port of 127.0.0.1 that the system picks now, until Stop.
func (p *Page) Listen(ctx context.Context) error {
	listener, err := (&net.ListenConfig{}).Listen(ctx, "tcp", "127.0.0.1:0")
	if err != nil {
		return fmt.Errorf("listen on 127.0.0.1: %w", err)
	}
	p.host = listener.Addr().String()
	p.server = &http.Server{Handler: p, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = p.server.Serve(listener) }()
	return nil
}

// Stop ends the listener, and waits for requests under way until ctx ends.
func (p *Page) Stop(ctx context.Context) {
	if p.server != nil {
		_ = p.server.Shutdown(ctx)
	}
}

// Mint is the page's URL carrying a fresh one-use code, and when the code stops opening it.
func (p *Page) Mint() (string, time.Time) {
	code := randomToken()
	expires := p.now().Add(CodeLifetime)
	p.mu.Lock()
	defer p.mu.Unlock()
	p.forgetExpired()
	p.codes[code] = expires
	return "http://" + p.host + "/?code=" + code, expires
}

// ServeHTTP refuses a foreign Host, trades a fresh code for the cookie, and
// answers a request that carries the cookie.
func (p *Page) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	header := response.Header()
	header.Set("Cache-Control", "no-store")
	header.Set("Referrer-Policy", "no-referrer")
	header.Set("X-Content-Type-Options", "nosniff")
	header.Set("Content-Security-Policy", contentPolicy)
	if request.Host != p.host {
		refuse(response, "the panel page answers only at "+p.host)
		return
	}
	if code := request.URL.Query().Get("code"); code != "" && request.URL.Path == "/" {
		if !p.redeem(code) {
			refuse(response, "the code that opens the panel page was used or has expired. `doit desk-nudge panel open` opens it again.")
			return
		}
		http.SetCookie(response, &http.Cookie{
			Name: cookieName, Value: p.admit(), Path: "/", HttpOnly: true, SameSite: http.SameSiteStrictMode,
		})
		http.Redirect(response, request, "/", http.StatusSeeOther)
		return
	}
	if !p.admitted(request) {
		refuse(response, "the panel page needs the cookie its opening code buys. `doit desk-nudge panel open` opens it.")
		return
	}
	if strings.HasPrefix(request.URL.Path, "/api/") {
		http.StripPrefix("/api", p.api).ServeHTTP(response, request)
		return
	}
	p.files.ServeHTTP(response, request)
}

// redeem spends code, and answers whether it was minted, unspent and unexpired.
func (p *Page) redeem(code string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.forgetExpired()
	for minted, expires := range p.codes {
		if subtle.ConstantTimeCompare([]byte(minted), []byte(code)) == 1 {
			delete(p.codes, minted)
			return p.now().Before(expires)
		}
	}
	return false
}

func (p *Page) forgetExpired() {
	now := p.now()
	for code, expires := range p.codes {
		if !now.Before(expires) {
			delete(p.codes, code)
		}
	}
}

// admit is a new session's cookie value, kept among the newest sessionsKept.
func (p *Page) admit() string {
	session := randomToken()
	p.mu.Lock()
	defer p.mu.Unlock()
	p.sessions = append(p.sessions, session)
	if len(p.sessions) > sessionsKept {
		p.sessions = p.sessions[len(p.sessions)-sessionsKept:]
	}
	return session
}

func (p *Page) admitted(request *http.Request) bool {
	cookie, err := request.Cookie(cookieName)
	if err != nil {
		return false
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, session := range p.sessions {
		if subtle.ConstantTimeCompare([]byte(session), []byte(cookie.Value)) == 1 {
			return true
		}
	}
	return false
}

func refuse(response http.ResponseWriter, message string) {
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(http.StatusForbidden)
	_, _ = fmt.Fprintln(response, message)
}

func randomToken() string {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		panic(errors.Join(errors.New("the system gave no random bytes"), err))
	}
	return base64.RawURLEncoding.EncodeToString(secret)
}
