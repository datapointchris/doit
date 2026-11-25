package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/deskfiles"
	"github.com/datapointchris/doit/cli/internal/service"
)

// editDocument edits one of the service's documents in the editor, starting from
// its template where none is written yet. The service validates the whole
// text and writes nothing it refuses. A refusal names the first fault and
// reopens the edit with the text as it was left. A save that fails any other
// way keeps the text on this desk, and the next edit starts from it. It
// reports whether a document now stands.
func editDocument(cmd *cobra.Command, client *service.Client, name string) (bool, error) {
	ctx, out, errOut := cmd.Context(), cmd.OutOrStdout(), cmd.ErrOrStderr()
	held, err := client.Document(ctx, name)
	stored := true
	if err != nil {
		var refused *service.Refused
		if !errors.As(err, &refused) || refused.Status != http.StatusNotFound {
			return false, err
		}
		if held, err = client.DocumentTemplate(ctx, name); err != nil {
			return false, err
		}
		stored = false
	}
	text, original := held.Text, held.Text
	kept := filepath.Join(deskfiles.StateDir(), name+"-unsaved.yml")
	if saved, err := os.ReadFile(kept); err == nil {
		text = string(saved)
		_, _ = fmt.Fprintln(errOut, yellow(errOut, fmt.Sprintf("%s: starting from the edit kept at %s, which was never saved.", name, kept)))
	}
	folder, err := os.MkdirTemp("", "doit-")
	if err != nil {
		return false, err
	}
	defer func() { _ = os.RemoveAll(folder) }()
	editing := filepath.Join(folder, name+".yml")
	for {
		if err := os.WriteFile(editing, []byte(text), 0o600); err != nil {
			return false, err
		}
		if err := openInEditor(ctx, editing); err != nil {
			return false, err
		}
		edited, err := os.ReadFile(editing)
		if err != nil {
			return false, err
		}
		text = string(edited)
		if text == original && stored {
			_ = os.Remove(kept)
			_, _ = fmt.Fprintf(out, "%s: no change.\n", name)
			return true, nil
		}
		err = client.SaveDocument(ctx, name, text)
		var refused *service.Refused
		switch {
		case err == nil:
			_ = os.Remove(kept)
			_, _ = fmt.Fprintf(out, "%s: saved.\n", name)
			return true, nil
		case errors.As(err, &refused) && refused.Status == http.StatusUnprocessableEntity:
			_, _ = fmt.Fprintln(errOut, yellow(errOut, name+": "+refused.Message))
			if !canAsk() {
				return false, nil
			}
			if answer, err := asked(out, "  Edit again? [Y/n] "); err != nil || strings.EqualFold(answer, "n") || strings.EqualFold(answer, "no") {
				return false, nil
			}
		default:
			if keepErr := deskfiles.Replace(kept, []byte(text)); keepErr != nil {
				return false, fmt.Errorf("%w, and the edit could not be kept at %s: %w", err, kept, keepErr)
			}
			return false, fmt.Errorf("%w, and the edit is kept at %s, where `doit %s edit` starts from it", err, kept, name)
		}
	}
}

// openInEditor opens path in the person's editor, as gotermboard names it:
// $VISUAL, then $EDITOR, then vi. It waits as long as the person edits.
func openInEditor(ctx context.Context, path string) error {
	editor := gotermboard.EditorCommand(ctx, path)
	editor.Stdin, editor.Stdout, editor.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := editor.Run(); err != nil {
		var exited *exec.ExitError
		if !errors.As(err, &exited) {
			return fmt.Errorf("could not open the editor %s: %w", editor.Path, err)
		}
	}
	return nil
}

// yellow is text in yellow where out renders color.
func yellow(out any, text string) string {
	file, _ := out.(*os.File)
	if file == nil {
		return text
	}
	return gotermstyle.PaletteFor(file).Paint(gotermstyle.CodeYellow, text)
}
