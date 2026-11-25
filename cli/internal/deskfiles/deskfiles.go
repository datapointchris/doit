// Package deskfiles says where doit keeps files on this desk, and writes the
// ones that another program reads while doit is not running.
package deskfiles

import (
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
)

// StateDir is doit's directory under $XDG_STATE_HOME, or ~/.local/state where that is unset.
func StateDir() string { return filepath.Join(base("XDG_STATE_HOME", ".local/state"), "doit") }

// CacheDir is doit's directory under $XDG_CACHE_HOME, or ~/.cache where that is unset.
func CacheDir() string { return filepath.Join(base("XDG_CACHE_HOME", ".cache"), "doit") }

func base(variable, fallback string) string {
	if dir := os.Getenv(variable); dir != "" {
		return dir
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, fallback)
}

// CompletionNamesPath is next-names.txt, which the zsh completion that
// `doit shell completion zsh` prints reads with no process started.
func CompletionNamesPath() string { return filepath.Join(CacheDir(), "next-names.txt") }

// WriteCompletionNames replaces the names file with one name<TAB>description
// line per pursuit, sorted by name. A description that is not a string is
// written as nothing.
func WriteCompletionNames(descriptions map[string]any) error {
	var lines strings.Builder
	for _, name := range slices.Sorted(maps.Keys(descriptions)) {
		said, _ := descriptions[name].(string)
		lines.WriteString(name + "\t" + said + "\n")
	}
	return Replace(CompletionNamesPath(), []byte(lines.String()))
}

// Replace writes path whole, readable by this user alone, through a temporary
// file beside it. A reader never sees half a file, and a failed write leaves
// the last one as it was.
func Replace(path string, content []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+"-*")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(temporary.Name()) }()
	if _, err := temporary.Write(content); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporary.Name(), path)
}
