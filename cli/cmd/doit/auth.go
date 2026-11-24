package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goclilogin"
	"github.com/pkg/browser"
	"github.com/spf13/cobra"
	"golang.org/x/oauth2"

	"github.com/datapointchris/doit/cli/internal/login"
)

// loginTimeout is a backstop only: the device code carries its own expiry, and
// the poll stops there first.
const loginTimeout = 15 * time.Minute

func newAuthCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "auth",
		Short: "Log this machine in to doit's service, and out",
		Long: "Log this machine in with the OAuth 2.0 device grant. doit prints a code and a URL,\n" +
			"and any browser on any device approves it. The token is kept in the OS keychain,\n" +
			"or in a file only this user reads where there is none.\n\n" +
			"Where doit is pointed comes from one config file. `doit auth status` prints its\n" +
			"path with its api, issuer and client_id, and `doit auth login <url>` fills in\n" +
			"whatever it lacks.",
		Example: "  doit auth status                          whether this machine is logged in, and to what\n" +
			"  doit auth login https://doit.example.com  log a new machine in to that service\n" +
			"  doit auth login                           log in again to the service the config file names\n" +
			"  doit auth logout                          forget this machine's token",
		GroupID: thisMachine,
	})
	cmd.Annotations[portedWhole] = "true"
	cmd.AddCommand(newAuthStatusCommand(), newAuthLoginCommand(), newAuthLogoutCommand(), newAuthTokenCommand())
	return cmd
}

func newAuthLoginCommand() *cobra.Command {
	var clientID string
	cmd := &cobra.Command{
		Use:   "login [url]",
		Short: "Log in by approving a code in any browser",
		Long: "Log in to the service at url, or to the config file's api where url is omitted.\n" +
			"Whatever the file lacks is filled in: api from url, the issuer from the service's\n" +
			"RFC 9728 metadata, and the client from --client-id or this machine's short\n" +
			"hostname. Each is printed with where it came from before the login starts, and\n" +
			"written to the file once it succeeds. A value already in the file is kept.",
		Example: "  doit auth login https://doit.example.com  log a new machine in to that service\n" +
			"  doit auth login                           log in again to the service the config file names",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			url := ""
			if len(args) == 1 {
				url = args[0]
			}
			ctx, cancel := context.WithTimeout(cmd.Context(), loginTimeout)
			defer cancel()

			deployment, err := login.ForLogin(ctx, url, clientID)
			if err != nil {
				return err
			}
			if deployment.HandsTokens() {
				return fmt.Errorf("%s sets token_command, so doit is handed its tokens and never logs in", deployment.Path)
			}
			stderr := cmd.ErrOrStderr()
			_, _ = fmt.Fprintln(stderr, "Logging in to:")
			goclilogin.WriteDeployment(stderr, deployment)
			_, _ = fmt.Fprintln(stderr)

			config := login.Config(deployment)
			store := goclilogin.NewTokenStore(config)

			// The browser's own start-up noise would land between the code and
			// the URL. A launcher that fails is still reported by OpenURL's error.
			browser.Stdout = io.Discard
			browser.Stderr = io.Discard

			token, err := goclilogin.Login(ctx, config, func(prompt goclilogin.DevicePrompt) {
				goclilogin.WriteInstructions(stderr, config.ClientID, prompt)
				if openErr := browser.OpenURL(prompt.BrowserURL()); openErr != nil {
					_, _ = fmt.Fprintf(stderr, "(could not open a browser here: %v)\n", openErr)
				}
			})
			if errors.Is(err, context.DeadlineExceeded) {
				return fmt.Errorf("timed out waiting for approval after %s; run `doit auth login` again", loginTimeout)
			}
			if err != nil {
				return err
			}
			backend, err := store.Save(config.ClientID, token)
			if err != nil {
				return fmt.Errorf("save the token: %w", err)
			}
			if backend == goclilogin.BackendFile {
				_, _ = fmt.Fprintf(stderr, "\nNo OS keyring on this host, so the token is in %s, which only this user reads.\n", store.FilePath())
			}
			if err := deployment.Save(); err != nil {
				return fmt.Errorf("logged in as %s, but the config file was not written: %w", config.ClientID, err)
			}
			_, _ = fmt.Fprintf(stderr, "\n✓ Logged in (%s)\n", config.ClientID)
			return nil
		},
	}
	cmd.Flags().StringVar(&clientID, "client-id", "",
		"Log in as this OAuth client where the config file names none (default doit-cli-<short hostname>)")
	return cmd
}

func newAuthLogoutCommand() *cobra.Command {
	return &cobra.Command{
		Use:     "logout",
		Short:   "Forget this machine's token",
		Long:    "Remove the stored token from the keychain and the fallback file. The grant is not revoked at the provider.",
		Example: "  doit auth logout  forget this machine's token",
		Args:    cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			// Only the client id finds the token, so a file lacking the other two
			// keys still logs out, and a file naming no client falls back to the
			// id login uses where none is given.
			deployment, err := login.Load(cmd.Context())
			if err != nil && !errors.Is(err, goclilogin.ErrNotConfigured) {
				return err
			}
			if deployment.HandsTokens() {
				return fmt.Errorf("%s sets token_command, so doit is handed its tokens and stores none to remove", deployment.Path)
			}
			if deployment.ClientID.Value == "" {
				deployment.ClientID.Value = login.DefaultClientID()
			}
			clientID := deployment.ClientID.Value
			err = goclilogin.NewTokenStore(login.Config(deployment)).Delete(clientID)
			if errors.Is(err, goclilogin.ErrNotLoggedIn) {
				_, _ = fmt.Fprintln(cmd.ErrOrStderr(), "Not logged in, so there is nothing to remove.")
				return nil
			}
			if err != nil {
				return fmt.Errorf("remove the token: %w", err)
			}
			_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Logged out (%s).\n", clientID)
			return nil
		},
	}
}

func newAuthTokenCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "token",
		Short: "Print a valid access token, for calling the service directly",
		Long: "Print a valid access token to stdout, refreshing it first where it has lapsed.\n" +
			"Pass it to curl on stdin, as the example does, so the token never appears in\n" +
			"another process's arguments. Exits 1 where this machine is not logged in.",
		Example: "  doit auth token\n" +
			"  printf 'Authorization: Bearer %s\\n' \"$(doit auth token)\" | curl --header @- https://doit.example.com/api/v1/documents",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			deployment, err := login.Load(cmd.Context())
			if err != nil {
				return err
			}
			source, err := login.TokenSource(cmd.Context(), deployment, goclilogin.NewTokenStore(login.Config(deployment)))
			if errors.Is(err, login.ErrNeedsLogin) {
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Not logged in as %s. Run `doit auth login`.\n", deployment.ClientID.Value)
				return exitCode(1)
			}
			if err != nil {
				return err
			}
			token, err := source.Token()
			if err != nil && deployment.HandsTokens() {
				return fmt.Errorf("token_command in %s handed no token: %w", deployment.Path, err)
			}
			if err != nil {
				return fmt.Errorf("no valid token; the refresh may have failed, and `doit auth login` replaces it: %w", err)
			}
			_, _ = fmt.Fprintln(cmd.OutOrStdout(), token.AccessToken)
			return nil
		},
	}
}

// credentialType is whose credential `auth status` checked, spelled as Google's
// credential files spell a person's OAuth login and an executable's token.
type credentialType string

const (
	authorizedUser  credentialType = "authorized_user"
	externalAccount credentialType = "external_account"
)

// statusReport is `auth status --json`. It reports what this machine holds and
// whether the provider honors it, never what the token claims about itself.
type statusReport struct {
	LoggedIn   bool                    `json:"logged_in"`
	Configured bool                    `json:"configured"`
	ConfigFile string                  `json:"config_file"`
	Type       credentialType          `json:"type"`
	API        string                  `json:"api"`
	ClientID   string                  `json:"client_id"`
	Issuer     string                  `json:"issuer"`
	ExpiresAt  string                  `json:"expires_at,omitempty"`
	Expired    bool                    `json:"expired"`
	Session    goclilogin.SessionState `json:"session"`
	Backend    goclilogin.Backend      `json:"backend,omitempty"`
	// KeyringNote is the keyring's own failure, where it is why no token was found.
	KeyringNote string `json:"keyring_note,omitempty"`
}

func newAuthStatusCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "status",
		Short: "Show where doit is pointed and whether this machine is logged in",
		Long: "Show the api, issuer and client_id this machine uses, where each came from, the\n" +
			"config file's path, and whether a token is stored for the client. A stored token\n" +
			"is checked the way a command uses it, refreshing it where it has lapsed. Exits 1\n" +
			"where the file lacks a value, no token is stored, or the provider refuses the\n" +
			"session.",
		Example: "  doit auth status         whether this machine is logged in, and to what\n" +
			"  doit auth status --json  the same as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			deployment, loadErr := login.Load(cmd.Context())
			if loadErr != nil && !errors.Is(loadErr, goclilogin.ErrNotConfigured) {
				return loadErr
			}
			report := statusReport{
				Configured: loadErr == nil,
				ConfigFile: deployment.Path,
				Type:       authorizedUser,
				API:        deployment.API.Value,
				ClientID:   deployment.ClientID.Value,
				Issuer:     deployment.Issuer.Value,
			}
			switch {
			case !report.Configured:
			case deployment.HandsTokens():
				report.Type = externalAccount
				handedStatus(cmd.Context(), deployment, &report)
			default:
				if err := loginStatus(cmd.Context(), deployment, &report); err != nil {
					return err
				}
			}
			if asJSON {
				encoder := json.NewEncoder(cmd.OutOrStdout())
				encoder.SetIndent("", "  ")
				if err := encoder.Encode(report); err != nil {
					return err
				}
			} else {
				printStatus(cmd.OutOrStdout(), report, deployment, loadErr)
			}
			// A rejected session exits 1 as being logged out does: nothing run
			// next works until a login. An unverified one exits 0, because its
			// stored token may still work.
			if !report.Configured || !report.LoggedIn || report.Session == goclilogin.SessionRejected {
				return exitCode(1)
			}
			return nil
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Print the status as JSON to stdout")
	return cmd
}

// loginStatus fills report from this machine's stored login.
func loginStatus(ctx context.Context, d goclilogin.Deployment, report *statusReport) error {
	config := login.Config(d)
	store := goclilogin.NewTokenStore(config)
	stored, backend, err := store.Load(d.ClientID.Value)
	switch {
	case errors.Is(err, goclilogin.ErrNotLoggedIn):
		// A host with no Secret Service is fixed by a login, and a locked
		// keychain is not, so the keyring's own failure is kept where it is why.
		if detail := strings.TrimPrefix(err.Error(), goclilogin.ErrNotLoggedIn.Error()); detail != "" {
			report.KeyringNote = strings.Trim(detail, " ()")
		}
		return nil
	case err != nil:
		return fmt.Errorf("read the token from the keychain: %w", err)
	default:
	}
	report.LoggedIn = true
	report.Backend = backend
	// Asking for a token the way a command does is the one thing that tells a
	// lapsed token from a revoked grant. A live token answers with no call to
	// the issuer, and a lapsed one makes the refresh the next command would.
	session, verified := goclilogin.VerifySession(ctx, config, store)
	report.Session = session
	if verified == nil {
		verified = stored
	}
	expiry(verified, report)
	return nil
}

// handedStatus asks token_command for a token, because nothing stored says
// whether the next command will get one.
func handedStatus(ctx context.Context, d goclilogin.Deployment, report *statusReport) {
	source, err := goclilogin.CommandTokenSource(ctx, d.TokenCommand.Argv())
	var token *oauth2.Token
	if err == nil {
		token, err = source.Token()
	}
	report.Session, token = goclilogin.ClassifySession(token, err)
	report.LoggedIn = report.Session == goclilogin.SessionLive
	expiry(token, report)
}

func expiry(token *oauth2.Token, report *statusReport) {
	if token == nil || token.Expiry.IsZero() {
		return
	}
	report.ExpiresAt = token.Expiry.Format(time.RFC3339)
	report.Expired = time.Now().After(token.Expiry)
}

// printStatus leads with the verdict on a line of its own, so a script checking
// a login reads the first line alone.
//
// WriteDeployment pads its keys to the widest, client_id, plus two, and the
// rows after it pad to the same column.
func printStatus(out io.Writer, r statusReport, d goclilogin.Deployment, loadErr error) {
	switch {
	case !r.Configured:
		_, _ = fmt.Fprintf(out, "%v\n", loadErr)
	case r.Type == externalAccount && r.LoggedIn:
		_, _ = fmt.Fprintln(out, "Handed a token by token_command")
	case r.Type == externalAccount:
		_, _ = fmt.Fprintf(out, "token_command handed no token. Check what %q hands out.\n", d.TokenCommand)
	case !r.LoggedIn:
		_, _ = fmt.Fprintf(out, "Not logged in as %s. Run `doit auth login` to authenticate.\n", r.ClientID)
	case r.Session == goclilogin.SessionRejected:
		_, _ = fmt.Fprintf(out, "Session refused by %s. Run `doit auth login` to authenticate.\n", r.Issuer)
	default:
		_, _ = fmt.Fprintln(out, "Logged in")
	}
	goclilogin.WriteDeployment(out, d)
	if r.KeyringNote != "" {
		_, _ = fmt.Fprintf(out, "  %-11s%s\n", "note", r.KeyringNote)
	}
	if r.ExpiresAt != "" {
		state := "valid"
		switch {
		case r.Type == externalAccount:
			state = "handed"
		case r.Expired:
			state = "expired, and refreshed on the next command"
		}
		_, _ = fmt.Fprintf(out, "  %-11s%s (expires %s)\n", "token", state, r.ExpiresAt)
	}
	if r.Backend == goclilogin.BackendFile {
		_, _ = fmt.Fprintf(out, "  %-11s%s, because this host has no OS keyring\n", "storage", r.Backend)
	}
	if r.Session == goclilogin.SessionUnverified && r.LoggedIn {
		_, _ = fmt.Fprintf(out, "  %-11sunverified, because %s could not be reached\n", "session", r.Issuer)
	}
}
