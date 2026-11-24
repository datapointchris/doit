package login

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"github.com/datapointchris/goclilogin"
)

// heldElsewhere reports whether another open file description holds an
// exclusive flock on path, which pyclilogin takes as well as goclilogin.
func heldElsewhere(path string) bool {
	file, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		return false
	}
	defer func() { _ = file.Close() }()
	err = syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
	if err == nil {
		_ = syscall.Flock(int(file.Fd()), syscall.LOCK_UN)
		return false
	}
	return errors.Is(err, syscall.EWOULDBLOCK)
}

func TestATokenInPycliloginsFormatIsReadAndRefreshedUnderTheSharedLock(t *testing.T) {
	state := t.TempDir()
	t.Setenv("XDG_STATE_HOME", state)
	const clientID = "doit-cli-testbox"
	pythonState := filepath.Join(state, "doit")

	var refreshedWith string
	lockHeld := false
	var issuer *httptest.Server
	issuer = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			_ = json.NewEncoder(w).Encode(map[string]string{
				"issuer":                        issuer.URL,
				"token_endpoint":                issuer.URL + "/token",
				"device_authorization_endpoint": issuer.URL + "/device",
			})
		case "/token":
			_ = r.ParseForm()
			refreshedWith = r.Form.Get("refresh_token")
			lockHeld = heldElsewhere(filepath.Join(pythonState, clientID+".refresh.lock"))
			_, _ = w.Write([]byte(`{"access_token": "go-access", "refresh_token": "go-refresh", "token_type": "Bearer", "expires_in": 3600}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer issuer.Close()

	// pyclilogin's fallback file, keyed by client id, holding Token.to_json:
	// an isoformat expiry with microseconds and an empty id_token.
	if err := os.MkdirAll(pythonState, 0o700); err != nil {
		t.Fatal(err)
	}
	written := `{"doit-cli-testbox": {"access_token": "py-access", "refresh_token": "py-refresh", "token_type": "Bearer",` +
		` "expiry": "2026-01-02T03:04:05.123456+00:00", "id_token": ""}}`
	if err := os.WriteFile(filepath.Join(pythonState, "token.json"), []byte(written), 0o600); err != nil {
		t.Fatal(err)
	}

	deployment := goclilogin.Deployment{
		Issuer:   goclilogin.Setting{Value: issuer.URL},
		ClientID: goclilogin.Setting{Value: clientID},
	}
	store := goclilogin.NewTestTokenStore(Config(deployment))

	read, _, err := store.Load(clientID)
	if err != nil {
		t.Fatalf("reading pyclilogin's token: %v", err)
	}
	if want := time.Date(2026, 1, 2, 3, 4, 5, 123456000, time.UTC); read.AccessToken != "py-access" || !read.Expiry.Equal(want) {
		t.Fatalf("read %q expiring %v; want py-access expiring %v", read.AccessToken, read.Expiry, want)
	}

	source, err := TokenSource(t.Context(), deployment, store)
	if err != nil {
		t.Fatal(err)
	}
	token, err := source.Token()
	if err != nil {
		t.Fatalf("refreshing: %v", err)
	}
	if token.AccessToken != "go-access" || refreshedWith != "py-refresh" {
		t.Errorf("got %q after refreshing with %q; want go-access after refreshing with py-refresh", token.AccessToken, refreshedWith)
	}
	if !lockHeld {
		t.Errorf("the refresh ran without holding %s, the lock pyclilogin takes", filepath.Join(pythonState, clientID+".refresh.lock"))
	}
	if stored, _, err := store.Load(clientID); err != nil || stored.RefreshToken != "go-refresh" {
		t.Errorf("stored %v, %v; want the rotated refresh token kept for the next command", stored, err)
	}
}
