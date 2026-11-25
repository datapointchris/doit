package desknudge

import (
	"bytes"
	"encoding/json"
	"time"
)

// RecordSchemaVersion is the shape of a line in desk-nudge-events.jsonl, which
// schemas/desk-nudge-record.schema.json describes.
const RecordSchemaVersion = 5

// Each moment the nudge records, one line each.
const (
	eventStarted  = "started"
	eventFired    = "fired"
	eventAnswered = "answered"
	eventExpired  = "expired"
	eventPicked   = "picked"
	eventGap      = "gap"
	eventPaused   = "paused"
	eventResumed  = "resumed"
)

// Record is one line of the records file. Its fields keep the order they were
// given, so ts, event and the schema version lead every line.
type Record struct {
	names  []string
	values []any
}

// newRecord is a line for event at now, with pairs of field names and values after it.
func newRecord(event string, now time.Time, pairs ...any) Record {
	r := Record{}
	r.set("ts", recordStamp(now))
	r.set("event", event)
	r.set("fleet.schema_version", RecordSchemaVersion)
	for i := 0; i+1 < len(pairs); i += 2 {
		r.set(pairs[i].(string), pairs[i+1])
	}
	return r
}

func (r *Record) set(name string, value any) {
	r.names = append(r.names, name)
	r.values = append(r.values, value)
}

// Event is the line's event.
func (r Record) Event() string {
	for i, name := range r.names {
		if name == "event" {
			event, _ := r.values[i].(string)
			return event
		}
	}
	return ""
}

func (r Record) MarshalJSON() ([]byte, error) {
	var out bytes.Buffer
	out.WriteByte('{')
	for i, name := range r.names {
		if i > 0 {
			out.WriteByte(',')
		}
		key, err := json.Marshal(name)
		if err != nil {
			return nil, err
		}
		value, err := marshalPlain(r.values[i])
		if err != nil {
			return nil, err
		}
		out.Write(key)
		out.WriteByte(':')
		out.Write(value)
	}
	out.WriteByte('}')
	return out.Bytes(), nil
}

// marshalPlain encodes value as JSON without escaping <, > and &, as a log line
// that a person reads keeps them.
func marshalPlain(value any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimRight(out.Bytes(), "\n"), nil
}

// answeredRecord is the answered line. A resend that stores or drops a queued
// answer carries no snapshot, because its moment has passed.
func answeredRecord(now time.Time, row AnswerRow, late, saved, queued bool, seen *snapshot) Record {
	fired, _ := instant(&row.FiredAt)
	answered, _ := instant(&row.AnsweredAt)
	r := newRecord(eventAnswered, now,
		"nudge_id", row.NudgeID,
		"answer", row.Answer,
		"seconds_since_fired", rounded(answered.Sub(fired).Seconds(), 3),
		"saved", saved,
		"queued", queued,
		"late", late,
	)
	seen.addTo(&r)
	return r
}

// pickedFields are what a picked line says of its pick: what it acted on, what
// it did, and whether it went through.
type pickedFields struct {
	Kind    string  `json:"kind"`
	Ref     string  `json:"ref"`
	ItemID  *string `json:"item_id"`
	Minutes *int    `json:"minutes"`
	Action  string  `json:"action"`
	Outcome string  `json:"outcome"`
	OK      bool    `json:"ok"`
	Error   *string `json:"error"`
}

// pickedRecord is the picked line: what the pick acted on, how it came out, and the nudge it ended.
func pickedRecord(now time.Time, nudgeID *string, fields pickedFields, seen *snapshot) Record {
	r := newRecord(eventPicked, now, "nudge_id", nudgeID, "pick", fields)
	seen.addTo(&r)
	return r
}
