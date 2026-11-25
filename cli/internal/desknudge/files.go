package desknudge

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"syscall"
	"time"
)

// Paths are the files the desk nudge keeps on this desk.
type Paths struct {
	// State is the clock, which the waybar pill reads.
	State string
	// StateLock is taken around each change to State.
	StateLock string
	// WatchLock is held for a watch's whole life, so a second one refuses to start.
	WatchLock string
	// Records is one line per moment the nudge records, appended and never rotated, for a log shipper.
	Records string
	// Panel is the panel as the watch last built it, which panel show reads with no watch.
	Panel string
	// Socket is where the watch answers the CLI.
	Socket string
}

// DeskPaths are this desk's, under $XDG_STATE_HOME, $XDG_CACHE_HOME and the runtime directory.
func DeskPaths() Paths {
	stateHome := xdg("XDG_STATE_HOME", ".local/state")
	state := filepath.Join(stateHome, "doit")
	return Paths{
		State:     filepath.Join(state, "desk-nudge.json"),
		StateLock: filepath.Join(state, "desk-nudge.lock"),
		WatchLock: filepath.Join(state, "desk-nudge-watch.lock"),
		Records:   filepath.Join(state, "desk-nudge-events.jsonl"),
		Panel:     filepath.Join(xdg("XDG_CACHE_HOME", ".cache"), "doit", "desk-nudge-panel.json"),
		Socket:    filepath.Join(runtimeDir(stateHome), "doit", "watch.sock"),
	}
}

// runtimeDir is $XDG_RUNTIME_DIR. Where a shell lacks it, as an ssh login can,
// it is logind's /run/user/<uid>, so the shell finds the socket of a watch
// that systemd started. With neither, it is the state home, which this user
// owns, rather than a shared temporary directory where another user could
// plant the socket.
func runtimeDir(stateHome string) string {
	if runtime := os.Getenv("XDG_RUNTIME_DIR"); runtime != "" {
		return runtime
	}
	logind := "/run/user/" + strconv.Itoa(os.Getuid())
	if info, err := os.Stat(logind); err == nil && info.IsDir() {
		return logind
	}
	return stateHome
}

func xdg(variable, fallback string) string {
	if dir := os.Getenv(variable); dir != "" {
		return dir
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, fallback)
}

// lockFile takes a POSIX record lock on the whole of path, the lock that
// Python's fcntl.lockf takes, so an older doit's Python watch still running
// through an upgrade and this watch exclude each other. With wait it blocks,
// and without it a lock held elsewhere is ErrHeldElsewhere.
func lockFile(path string, wait bool) (*os.File, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	command := syscall.F_SETLK
	if wait {
		command = syscall.F_SETLKW
	}
	lock := syscall.Flock_t{Type: syscall.F_WRLCK, Whence: io.SeekStart}
	for {
		err = syscall.FcntlFlock(file.Fd(), command, &lock)
		if !errors.Is(err, syscall.EINTR) {
			break
		}
	}
	if err != nil {
		_ = file.Close()
		if errors.Is(err, syscall.EAGAIN) || errors.Is(err, syscall.EACCES) {
			return nil, ErrHeldElsewhere
		}
		return nil, err
	}
	return file, nil
}

// ErrHeldElsewhere is a lock that another process holds.
var ErrHeldElsewhere = errors.New("held by another process")

// HoldWatchLock holds the watch lock until the returned file closes, or answers
// ErrHeldElsewhere where another watch holds it.
func (p Paths) HoldWatchLock() (*os.File, error) { return lockFile(p.WatchLock, false) }

// watchHeld is whether another process holds the watch lock now.
func (p Paths) watchHeld() bool {
	lock, err := p.HoldWatchLock()
	if err != nil {
		return errors.Is(err, ErrHeldElsewhere)
	}
	_ = lock.Close()
	return false
}

// replaceFile replaces a JSON file whole, through a temporary file beside it,
// so no reader sees half of one.
func replaceFile(path string, document any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	encoded, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".desk-nudge-*.json")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(temporary.Name()) }()
	if _, err := temporary.Write(append(encoded, '\n')); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporary.Name(), path)
}

// stateMutex keeps this process's goroutines apart. A POSIX lock excludes other
// processes and never this one, so the goroutines take this first.
var stateMutex sync.Mutex

// readState is the state for a reader that changes nothing. A file that cannot
// be read reads as a fresh desk's.
func (p Paths) readState() *State {
	text, err := os.ReadFile(p.State)
	if err != nil {
		return freshState()
	}
	state, err := parseState(text)
	if err != nil {
		return freshState()
	}
	return state
}

// change reads the state under the state lock, hands it to apply, and writes it
// back with the pill redrawn. An apply that returns an error writes nothing. A
// file that cannot be read is set aside, and the change starts from a fresh clock.
func (p Paths) change(apply func(*State) error) error {
	stateMutex.Lock()
	defer stateMutex.Unlock()
	lock, err := lockFile(p.StateLock, true)
	if err != nil {
		return fmt.Errorf("lock %s: %w", p.StateLock, err)
	}
	defer func() { _ = lock.Close() }()
	state := freshState()
	text, err := os.ReadFile(p.State)
	switch {
	case errors.Is(err, fs.ErrNotExist):
	case err != nil:
		return err
	default:
		if parsed, parseErr := parseState(text); parseErr == nil {
			state = parsed
		} else {
			aside := fmt.Sprintf("%s.unreadable-%s", p.State, time.Now().Format("20060102T150405"))
			if renameErr := os.Rename(p.State, aside); renameErr != nil {
				return renameErr
			}
			logger.Error(fmt.Sprintf("%s cannot be read, so it is moved to %s and the clock starts fresh: %v", p.State, aside, parseErr))
		}
	}
	if err := apply(state); err != nil {
		return err
	}
	state.drawPill()
	return replaceFile(p.State, state)
}

// appendRecords appends each line. A failed append logs at error, and whatever
// wrote it carries on.
func (p Paths) appendRecords(records ...Record) {
	if len(records) == 0 {
		return
	}
	var lines []byte
	for _, record := range records {
		line, err := json.Marshal(record)
		if err != nil {
			logger.Error("could not encode a desk nudge record: " + err.Error())
			continue
		}
		lines = append(append(lines, line...), '\n')
	}
	err := os.MkdirAll(filepath.Dir(p.Records), 0o755)
	var file *os.File
	if err == nil {
		file, err = os.OpenFile(p.Records, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	}
	if err == nil {
		_, err = file.Write(lines)
		if closeErr := file.Close(); err == nil {
			err = closeErr
		}
	}
	if err != nil {
		logger.Error(fmt.Sprintf("could not append %d desk nudge record(s) to %s: %v", len(records), p.Records, err))
	}
}
