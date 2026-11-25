//go:build unix

package contract

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// token is the one token the service accepts, conftest.TOKEN in api/tests.
const token = "person-token"

// service is the Python service, started from api/tests/serve_on_a_temporary_store.py.
type service struct {
	url    string
	stderr *bytes.Buffer
}

// startService serves this commit's service on a temporary store until the test ends.
func startService(t *testing.T, root string) service {
	t.Helper()
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatal("uv is not on PATH, and the contract test starts the service with it")
	}
	cmd := exec.CommandContext(t.Context(), uv, "run", "--quiet", "--project", filepath.Join(root, "api"), "python",
		filepath.Join(root, "api", "tests", "serve_on_a_temporary_store.py"), filepath.Join(t.TempDir(), "doit.db"))
	cmd.Dir = root
	// uv starts python as its child, so the whole group is stopped at the end.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting the service: %v", err)
	}
	t.Cleanup(func() {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		_ = cmd.Wait()
	})

	ported := make(chan string, 1)
	go func() {
		first, _ := bufio.NewReader(stdout).ReadString('\n')
		ported <- strings.TrimSpace(first)
	}()
	var port string
	select {
	case port = <-ported:
	case <-time.After(5 * time.Minute):
		t.Fatalf("the service named no port within 5 minutes: %s", stderr)
	}
	if port == "" {
		t.Fatalf("the service exited before naming its port: %s", stderr)
	}
	served := service{url: "http://127.0.0.1:" + port, stderr: stderr}
	deadline := time.Now().Add(30 * time.Second)
	for {
		request, err := http.NewRequestWithContext(t.Context(), http.MethodGet, served.url+"/health", http.NoBody)
		if err != nil {
			t.Fatal(err)
		}
		response, err := http.DefaultClient.Do(request)
		if err == nil {
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK {
				return served
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("the service at %s never answered /health: %v\n%s", served.url, err, stderr)
		}
		time.Sleep(100 * time.Millisecond)
	}
}

// write stores the file at path as a document, as `doit pursuits edit` and its siblings do.
func (s service) write(t *testing.T, name, path string) {
	t.Helper()
	text, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s.writeText(t, name, string(text))
}

// writeText stores text as a document.
func (s service) writeText(t *testing.T, name, text string) {
	t.Helper()
	body, _ := json.Marshal(map[string]string{"text": text})
	response := s.send(t, http.MethodPut, "/api/v1/documents/"+name, bytes.NewReader(body))
	_ = response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("PUT %s answered %d: %s", name, response.StatusCode, s.stderr)
	}
}

// read decodes what GET path answers into answer, to see what a verb wrote.
func (s service) read(t *testing.T, path string, answer any) {
	t.Helper()
	response := s.send(t, http.MethodGet, path, http.NoBody)
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK || json.NewDecoder(response.Body).Decode(answer) != nil {
		t.Fatalf("GET %s answered %d: %s", path, response.StatusCode, s.stderr)
	}
}

func (s service) send(t *testing.T, method, path string, body io.Reader) *http.Response {
	t.Helper()
	request, err := http.NewRequestWithContext(t.Context(), method, s.url+path, body)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	return response
}

// desk is a machine pointed at the service, with its own home and a token command for its login.
type desk struct {
	binary string
	home   string
	env    []string
}

// newDesk builds this commit's doit and points a temporary home at url.
func newDesk(t *testing.T, url string) desk {
	t.Helper()
	home := t.TempDir()
	binary := filepath.Join(home, "doit")
	build := exec.CommandContext(t.Context(), "go", "build", "-o", binary, "github.com/datapointchris/doit/cli/cmd/doit")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("building doit: %v\n%s", err, out)
	}
	answer := fmt.Sprintf(`{"access_token":%q,"token_type":"Bearer","expires_in":3600}`, token)
	config := fmt.Sprintf("api = %q\ntoken_command = [\"printf\", \"%%s\", %q]\n", url, answer)
	if err := os.MkdirAll(filepath.Join(home, "config", "doit"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, "config", "doit", "config.toml"), []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	return desk{
		binary: binary,
		home:   home,
		env: []string{
			"HOME=" + home,
			"PATH=" + os.Getenv("PATH"),
			"XDG_CONFIG_HOME=" + filepath.Join(home, "config"),
			"XDG_CACHE_HOME=" + filepath.Join(home, "cache"),
			"XDG_STATE_HOME=" + filepath.Join(home, "state"),
			"COLUMNS=120",
			"NO_COLOR=1",
		},
	}
}

// run is doit run with args on the desk, and what it printed and exited with.
func (d desk) run(t *testing.T, args ...string) (string, string, int) {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, d.binary, args...)
	cmd.Env = d.env
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exited *exec.ExitError
	if err != nil && !errors.As(err, &exited) {
		t.Fatalf("doit %v: %v", args, err)
	}
	return stdout.String(), stderr.String(), cmd.ProcessState.ExitCode()
}
