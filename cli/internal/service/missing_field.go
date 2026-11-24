package service

import (
	"fmt"
	"reflect"
	"slices"
	"strings"
)

// missingFieldOf names the first field that answer's type needs and parsed
// lacks, by its path in the JSON, such as rows[2].minutes. It is "" where
// parsed carries every one.
//
// answer is a pointer to the route's answer type. A struct field is
// needed unless its tag says omitempty or omitzero. A field present as null
// counts as present, because the service sends null for "none".
func missingFieldOf(answer any, parsed any) string {
	return missingField(reflect.TypeOf(answer), parsed, "")
}

func missingField(t reflect.Type, value any, at string) string {
	for t != nil && t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t == nil {
		return ""
	}
	switch t.Kind() {
	case reflect.Struct:
		object, ok := value.(map[string]any)
		if !ok {
			return ""
		}
		return missingInObject(t, object, at)
	case reflect.Slice, reflect.Array:
		items, _ := value.([]any)
		for index, item := range items {
			if found := missingField(t.Elem(), item, fmt.Sprintf("%s[%d]", at, index)); found != "" {
				return found
			}
		}
	case reflect.Map:
		entries, _ := value.(map[string]any)
		keys := make([]string, 0, len(entries))
		for key := range entries {
			keys = append(keys, key)
		}
		slices.Sort(keys)
		for _, key := range keys {
			if found := missingField(t.Elem(), entries[key], joined(at, key)); found != "" {
				return found
			}
		}
	default:
	}
	return ""
}

func missingInObject(t reflect.Type, object map[string]any, at string) string {
	for index := range t.NumField() {
		field := t.Field(index)
		if !field.IsExported() {
			continue
		}
		tag, hasTag := field.Tag.Lookup("json")
		name, options, _ := strings.Cut(tag, ",")
		if name == "-" && options == "" {
			continue
		}
		if field.Anonymous && name == "" {
			if found := missingField(field.Type, object, at); found != "" {
				return found
			}
			continue
		}
		if !hasTag || name == "" {
			name = field.Name
		}
		value, present := object[name]
		if !present {
			if optional(options) {
				continue
			}
			return joined(at, name)
		}
		if found := missingField(field.Type, value, joined(at, name)); found != "" {
			return found
		}
	}
	return ""
}

func optional(options string) bool {
	for option := range strings.SplitSeq(options, ",") {
		if option == "omitempty" || option == "omitzero" {
			return true
		}
	}
	return false
}

func joined(at, name string) string {
	if at == "" {
		return name
	}
	return at + "." + name
}
