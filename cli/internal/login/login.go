// Package login is doit's view of goclilogin: where the service is, which
// client this machine logs in as, and where its token and refresh lock live.
//
// The Python doit wrote this machine's login, and still reads it for each
// unported verb. So each identifier below matches pyclilogin's for the product
// "doit": the config file, the keychain entry, the fallback token file and the
// refresh lock. Both sides then take one lock. After either side refreshes, the
// other reads the new token rather than replaying the rotated one.
package login

import (
	"context"
	"errors"

	"github.com/datapointchris/goclilogin"
	"golang.org/x/oauth2"
)

// Product names the config file's directory and the state directory.
const Product = "doit"

// KeyringService namespaces doit's entries in the OS keychain. It is a deployed
// identifier: renaming it orphans every stored token.
const KeyringService = "doit-cli"

// Load reads the service's deployment from the config file.
func Load(ctx context.Context) (goclilogin.Deployment, error) {
	return goclilogin.LoadDeployment(ctx, Product, "")
}

// ForLogin is the deployment `doit auth login` logs in to: the file's values,
// with whatever it lacks filled in from api, the service's metadata and clientID.
func ForLogin(ctx context.Context, api, clientID string) (goclilogin.Deployment, error) {
	return goclilogin.DeploymentForLogin(ctx, Product, api, clientID)
}

// Path is where the config file goes, whether or not it exists.
func Path() string { return goclilogin.ConfigPath(Product) }

// DefaultClientID is the client login names where neither the file nor
// --client-id does: doit-cli-<short hostname>.
func DefaultClientID() string { return goclilogin.ClientID(Product) }

// Config is the goclilogin view of a deployment.
//
// StateDir is pyclilogin's, $XDG_STATE_HOME/doit, rather than goclilogin's
// default under the keyring service's name. Two directories would mean two
// refresh locks, and a refresh token rotated by one side would then fail on
// the other.
func Config(d goclilogin.Deployment) goclilogin.Config {
	return goclilogin.Config{
		Issuer:         d.Issuer.Value,
		ClientID:       d.ClientID.Value,
		KeyringService: KeyringService,
		StateDir:       goclilogin.StateDir(Product),
	}
}

// ErrNeedsLogin is a person's machine holding no token, which `doit auth login` fixes.
var ErrNeedsLogin = errors.New("this machine is not logged in to doit's service; run `doit auth login`")

// TokenSource is where every request's token comes from: the config file's
// token_command where it sets one, else the token this machine logged in for.
func TokenSource(ctx context.Context, d goclilogin.Deployment, store *goclilogin.TokenStore) (oauth2.TokenSource, error) {
	if d.HandsTokens() {
		return goclilogin.CommandTokenSource(ctx, d.TokenCommand.Argv())
	}
	source, err := goclilogin.TokenSource(ctx, Config(d), store)
	if errors.Is(err, goclilogin.ErrNotLoggedIn) {
		return nil, ErrNeedsLogin
	}
	return source, err
}
