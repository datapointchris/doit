package main

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// A command printed for a person to run: after rich markup, in a code span or
// in single quotes in the Python source, or on a line of a shell fence in the
// README.
var (
	printedInSource = regexp.MustCompile("(?:\\]|`|')doit")
	shellFence      = regexp.MustCompile("(?ms)^```(?:bash|sh)\n(.*?)\n```")
	printedInFence  = regexp.MustCompile("(?m)^doit")
)

// commandWords reads the words of a command printed just before text. Each is
// one space, a lowercase letter, then letters, digits, `_` or `-`. A word
// running into a `/` or a `.` is a path argument, so the words stop before it.
func commandWords(text string) []string {
	var words []string
	for len(text) > 1 && text[0] == ' ' && 'a' <= text[1] && text[1] <= 'z' {
		end := 2
		for end < len(text) && isCommandWordByte(text[end]) {
			end++
		}
		if end < len(text) && (text[end] == '/' || text[end] == '.') {
			break
		}
		words = append(words, text[1:end])
		text = text[end:]
	}
	return words
}

func isCommandWordByte(b byte) bool {
	return 'a' <= b && b <= 'z' || 'A' <= b && b <= 'Z' || '0' <= b && b <= '9' || b == '_' || b == '-'
}

type printedCommand struct {
	source string
	words  []string
}

func printedCommands(t *testing.T, repo string) []printedCommand {
	t.Helper()
	var printed []printedCommand
	add := func(source, text string, starts *regexp.Regexp) {
		for _, at := range starts.FindAllStringIndex(text, -1) {
			if words := commandWords(text[at[1]:]); len(words) > 0 {
				printed = append(printed, printedCommand{source, words})
			}
		}
	}
	sources, err := filepath.Glob(filepath.Join(repo, "src", "doit", "*.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, source := range sources {
		text, err := os.ReadFile(source)
		if err != nil {
			t.Fatal(err)
		}
		add(filepath.Base(source), string(text), printedInSource)
	}
	readme, err := os.ReadFile(filepath.Join(repo, "README.md"))
	if err != nil {
		t.Fatal(err)
	}
	for _, fence := range shellFence.FindAllStringSubmatch(string(readme), -1) {
		add("README.md", fence[1], printedInFence)
	}
	add("README.md", string(readme), printedInSource)
	return printed
}

// A hint naming a command that was renamed sends its reader to an error. The
// walk stops at a leaf, so the words after it are that command's arguments.
func TestEveryCommandTheRepoPrintsIsInTheCommandTree(t *testing.T) {
	printed := printedCommands(t, filepath.Join("..", "..", ".."))
	var missing []string
	for _, each := range printed {
		command := newRootCommand()
		for _, word := range each.words {
			if !command.HasSubCommands() {
				break
			}
			next := slices.IndexFunc(command.Commands(), func(child *cobra.Command) bool {
				return child.Name() == word || child.HasAlias(word)
			})
			if next < 0 {
				missing = append(missing, each.source+": doit "+strings.Join(each.words, " "))
				break
			}
			command = command.Commands()[next]
		}
	}
	if len(printed) < 20 {
		t.Errorf("found %d printed commands, so the patterns no longer match how the repo prints them", len(printed))
	}
	if len(missing) > 0 {
		t.Errorf("printed commands that the tree lacks:\n%s", strings.Join(missing, "\n"))
	}
}
