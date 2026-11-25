package desknudge

import (
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/datapointchris/doit/cli/internal/panelpage"
)

// pageRequest sends one request to the panel page, following no redirect.
func pageRequest(t *testing.T, method, target, host string, cookie *http.Cookie, body string) *http.Response {
	t.Helper()
	request, err := http.NewRequestWithContext(t.Context(), method, target, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if host != "" {
		request.Host = host
	}
	if cookie != nil {
		request.AddCookie(cookie)
	}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	return response
}

func TestAForeignHostAMissingCookieAReusedCodeAndAnExpiredCodeAreEachRefused(t *testing.T) {
	f := newFakeDesk(t)
	watch := f.started()
	f.serving(watch)
	mint := func() string {
		var minted PageCode
		if status := f.askSocket(http.MethodPost, "/page-codes", nil, &minted); status != http.StatusCreated {
			t.Fatalf("POST /page-codes answered %d, want a code", status)
		}
		return minted.URL
	}

	opened := mint()
	page, _ := url.Parse(opened)
	root := page.Scheme + "://" + page.Host
	first := pageRequest(t, http.MethodGet, opened, "", nil, "")
	var cookie *http.Cookie
	for _, set := range first.Cookies() {
		cookie = set
	}
	if first.StatusCode != http.StatusSeeOther || cookie == nil || cookie.SameSite != http.SameSiteStrictMode || !cookie.HttpOnly {
		t.Fatalf("the first load answered %d with cookie %+v, want a 303 setting an HttpOnly SameSite=Strict cookie", first.StatusCode, cookie)
	}
	if got := pageRequest(t, http.MethodGet, root+"/api/panel", "", cookie, ""); got.StatusCode != http.StatusOK {
		t.Fatalf("the panel with the cookie answered %d, want 200", got.StatusCode)
	}

	for _, refused := range []struct {
		why                  string
		method, target, host string
		cookie               *http.Cookie
		body                 string
	}{
		{"a foreign Host", http.MethodGet, root + "/api/panel", "doit.example:" + page.Port(), cookie, ""},
		{"a missing cookie", http.MethodGet, root + "/api/panel", "", nil, ""},
		{"a missing cookie on a click", http.MethodPost, root + "/api/picks", "", nil, `{"key": "review:prune-images"}`},
		{"a reused code", http.MethodGet, opened, "", nil, ""},
	} {
		if got := pageRequest(t, refused.method, refused.target, refused.host, refused.cookie, refused.body); got.StatusCode != http.StatusForbidden {
			t.Errorf("%s answered %d, want 403", refused.why, got.StatusCode)
		}
	}
	if len(f.reviews) != 0 {
		t.Errorf("a click with no cookie marked %v done, want nothing picked", f.reviews)
	}

	expired := mint()
	f.advance(panelpage.CodeLifetime)
	if got := pageRequest(t, http.MethodGet, expired, "", nil, ""); got.StatusCode != http.StatusForbidden || len(got.Cookies()) != 0 {
		t.Errorf("an expired code answered %d with cookies %v, want 403 and no cookie", got.StatusCode, got.Cookies())
	}
	if got := pageRequest(t, http.MethodPost, root+"/api/picks", "", cookie, `{"key": "review:prune-images"}`); got.StatusCode != http.StatusOK {
		t.Errorf("a click with the cookie answered %d, want the pick done", got.StatusCode)
	}
	// The page's row shows a pick that went through, so no notice repeats it.
	if notices := f.ran("notify-send"); len(notices) != 0 {
		t.Errorf("notify-send ran %q for a click that went through, want nothing", notices)
	}
}
