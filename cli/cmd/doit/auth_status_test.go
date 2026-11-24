package main

import (
	"encoding/json"
	"maps"
	"os"
	"slices"
	"testing"
)

func TestAuthStatusJSONCarriesExactlyTheFieldsItsSchemaFileNames(t *testing.T) {
	data, err := os.ReadFile("../../../schemas/auth-status.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Required   []string                   `json:"required"`
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if err := json.Unmarshal(data, &schema); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(statusReport{})
	if err != nil {
		t.Fatal(err)
	}
	var written map[string]any
	if err := json.Unmarshal(encoded, &written); err != nil {
		t.Fatal(err)
	}
	fields := slices.Sorted(maps.Keys(written))
	required := slices.Sorted(slices.Values(schema.Required))
	properties := slices.Sorted(maps.Keys(schema.Properties))
	if !slices.Equal(fields, required) || !slices.Equal(fields, properties) {
		t.Errorf("auth status --json writes %q; the schema requires %q and describes %q", fields, required, properties)
	}
}
