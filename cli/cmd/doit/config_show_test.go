package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// A plausible value is the failure this read exists to expose, so each value
// carries where it came from.
func TestConfigShowSaysWhereEachSettingCameFromAndMatchesItsSchema(t *testing.T) {
	config := t.TempDir()
	if err := os.MkdirAll(filepath.Join(config, "doit"), 0o755); err != nil {
		t.Fatal(err)
	}
	file := "api = \"https://doit.example.com\"\nissuer = \"https://id.example.com\"\nclient_id = \"c\"\n"
	if err := os.WriteFile(filepath.Join(config, "doit", "config.toml"), []byte(file), 0o600); err != nil {
		t.Fatal(err)
	}
	run := runDoitWith(t, []string{"XDG_CONFIG_HOME=" + config, "DOIT_CLIENT_SECRET=never-print-this", "VISUAL=", "EDITOR=nvim  -p"},
		"config", "show", "--json")
	if run.exit != 0 || strings.Contains(run.stdout, "never-print-this") {
		t.Fatalf("doit config show --json exited %d, printing:\n%s%s", run.exit, run.stdout, run.stderr)
	}

	schema, err := jsonschema.NewCompiler().Compile(filepath.Join("..", "..", "..", "schemas", "config-show.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	printed, err := jsonschema.UnmarshalJSON(strings.NewReader(run.stdout))
	if err != nil {
		t.Fatal(err)
	}
	if err := schema.Validate(printed); err != nil {
		t.Errorf("doit config show --json does not match its schema: %v", err)
	}

	var document struct {
		Settings map[string]setting `json:"settings"`
	}
	if err := json.Unmarshal([]byte(run.stdout), &document); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]setting{
		"api":           {"https://doit.example.com", "file", filepath.Join(config, "doit", "config.toml")},
		"config_file":   {filepath.Join(config, "doit", "config.toml"), "environment", "$XDG_CONFIG_HOME"},
		"client_secret": {"set", "environment", "$DOIT_CLIENT_SECRET"},
		"editor":        {"nvim -p", "environment", "$EDITOR"},
		"token_command": {},
	} {
		if got := document.Settings[name]; got != want {
			t.Errorf("%s reads %+v, want %+v", name, got, want)
		}
	}

	if unconfigured := runDoitWith(t, nil, "config", "show"); unconfigured.exit != 0 {
		t.Errorf("doit config show with no config file exited %d:\n%s%s", unconfigured.exit, unconfigured.stdout, unconfigured.stderr)
	}
}
