package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/datapointchris/goclikit"
	"github.com/datapointchris/goclilogin"
	"github.com/datapointchris/gotermboard"
	"github.com/datapointchris/gotermboard/printedtable"
	"github.com/datapointchris/gotermstyle"
	"github.com/spf13/cobra"

	"github.com/datapointchris/doit/cli/internal/actor"
	"github.com/datapointchris/doit/cli/internal/deskfiles"
	"github.com/datapointchris/doit/cli/internal/login"
)

// configShowVersion is the schema_version of schemas/config-show.schema.json.
const configShowVersion = 1

// codeDimmed is SGR 2, the faint text gotermstyle has no code for.
const codeDimmed = "\033[2m"

func newConfigCommand() *cobra.Command {
	cmd := goclikit.AsNamespace(&cobra.Command{
		Use:   "config",
		Short: "Where this machine points doit, and the files it keeps",
		Example: "  doit config show         every setting this machine resolved, and where each came from\n" +
			"  doit config show --json  the same settings as data",
		GroupID: thisMachine,
	})
	cmd.AddCommand(newConfigShowCommand())
	return cmd
}

func newConfigShowCommand() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "show",
		Short: "Every setting this machine resolved, and where each came from",
		Long: "Every setting this machine resolved, and where each came from.\n\n" +
			"The values doit acts on, not a copy of the config file. `doit auth status` says\n" +
			"whether the login behind them is still good.",
		Example: "  doit config show         what doit will use on this machine\n" +
			"  doit config show --json  the same as data",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			deployment, err := login.Load(cmd.Context())
			unconfigured := errors.Is(err, goclilogin.ErrNotConfigured)
			if err != nil && !unconfigured {
				return err
			}
			resolved := resolvedSettings(deployment)
			if asJSON {
				return printJSON(cmd.OutOrStdout(), configDocument{SchemaVersion: configShowVersion, Settings: resolved})
			}
			return printSettings(cmd.OutOrStdout(), resolved, unconfigured)
		},
	}
	cmd.Flags().BoolVar(&asJSON, "json", false, "Output as JSON to stdout")
	return cmd
}

// setting is one value doit resolved, the kind of place that set it, and
// which one. An empty value means nothing set it.
type setting struct {
	Value  string `json:"value"`
	Source string `json:"source"`
	From   string `json:"from"`
}

// configDocument is the --json answer, which schemas/config-show.schema.json describes.
type configDocument struct {
	SchemaVersion int            `json:"schema_version"`
	Settings      configSettings `json:"settings"`
}

// namedSetting is one setting under the name both the screen and --json give it.
type namedSetting struct {
	name string
	setting
}

// configSettings is every value doit resolves on this machine, in the order the
// screen lists them and --json writes them.
type configSettings []namedSetting

// MarshalJSON writes the settings as one object keyed by name, in their order.
func (settings configSettings) MarshalJSON() ([]byte, error) {
	var object bytes.Buffer
	object.WriteByte('{')
	for i, each := range settings {
		if i > 0 {
			object.WriteByte(',')
		}
		name, err := json.Marshal(each.name)
		if err != nil {
			return nil, err
		}
		value, err := json.Marshal(each.setting)
		if err != nil {
			return nil, err
		}
		object.Write(name)
		object.WriteByte(':')
		object.Write(value)
	}
	object.WriteByte('}')
	return object.Bytes(), nil
}

func resolvedSettings(d goclilogin.Deployment) configSettings {
	var tokenCommand, clientSecret setting
	if d.HandsTokens() {
		tokenCommand = setting{Value: d.TokenCommand.String(), Source: string(goclilogin.SourceFile), From: d.Path}
	}
	// Whether it is set, and never what it is.
	if os.Getenv(login.ClientSecretVariable) != "" {
		clientSecret = setting{Value: "set", Source: string(goclilogin.SourceEnvironment), From: "$" + login.ClientSecretVariable}
	}
	identity, identitySource := actor.IdentityAndSource()
	return configSettings{
		{"api", fromLogin(d.API)},
		{"issuer", fromLogin(d.Issuer)},
		{"client_id", fromLogin(d.ClientID)},
		{"token_command", tokenCommand},
		{"client_secret", clientSecret},
		{"config_file", fromVariable(login.Path(), "XDG_CONFIG_HOME")},
		{"cache_dir", fromVariable(deskfiles.CacheDir(), "XDG_CACHE_HOME")},
		{"state_dir", fromVariable(deskfiles.StateDir(), "XDG_STATE_HOME")},
		{"editor", editorSetting()},
		{"machine", setting{Value: actor.MachineName(), Source: string(goclilogin.SourceHostname)}},
		{"actor", setting{Value: identity, Source: string(goclilogin.SourceEnvironment), From: identitySource}},
	}
}

func fromLogin(s goclilogin.Setting) setting {
	return setting{Value: s.Value, Source: string(s.Source), From: s.From}
}

// fromVariable is value, set by variable where that is set and not empty, and a default otherwise.
func fromVariable(value, variable string) setting {
	if os.Getenv(variable) != "" {
		return setting{Value: value, Source: string(goclilogin.SourceEnvironment), From: "$" + variable}
	}
	return setting{Value: value, Source: "default"}
}

// editorSetting is what gotermboard's EditorArgs opens for every edit verb, and
// the variable that names it where one does.
func editorSetting() setting {
	argv := gotermboard.EditorArgs("")
	editor := strings.Join(argv[:len(argv)-1], " ")
	for _, variable := range []string{"VISUAL", "EDITOR"} {
		if strings.Join(strings.Fields(os.Getenv(variable)), " ") == editor {
			return fromVariable(editor, variable)
		}
	}
	return setting{Value: editor, Source: "default"}
}

// reads is where a setting came from, as the screen says it.
func (s setting) reads() string {
	switch {
	case s.Value == "":
		return "not set"
	case s.Source == string(goclilogin.SourceFile):
		return "config file"
	case s.Source == string(goclilogin.SourceEnvironment):
		return s.From
	}
	return s.Source
}

func printSettings(out io.Writer, resolved configSettings, unconfigured bool) error {
	palette := gotermstyle.PaletteFor(out)
	table := printedtable.Table{}
	for _, row := range resolved {
		value := row.Value
		if value == "" {
			value = "-"
		}
		table.Rows = append(table.Rows, []printedtable.Cell{
			printedtable.C(row.name, gotermstyle.CodeWhite),
			printedtable.C(value, ""),
			printedtable.C(row.reads(), codeDimmed),
		})
	}
	var screen strings.Builder
	for _, line := range table.Lines(palette) {
		screen.WriteString("  " + line + "\n")
	}
	if unconfigured {
		fmt.Fprintf(&screen, "\n  %s fills in what the config file lacks.\n", palette.Paint(gotermstyle.CodeCommand, "doit auth login <url>"))
	}
	_, err := io.WriteString(out, screen.String())
	return err
}
