// Package actor names who a write is recorded as: this machine, and who is at it.
//
// slate owns the identity's grammar, in actorIdentity in its
// cli/internal/cli/actor_identity.go, and Identity follows it, so a journal
// entry and an issue close read alike. A change to the grammar starts there.
package actor

import (
	"os"
	"strings"
)

// MachineName is this machine's identity: its hostname's first label, lowercased.
func MachineName() string {
	host, _ := os.Hostname()
	first, _, _ := strings.Cut(host, ".")
	return strings.ToLower(strings.TrimSpace(first))
}

// Identity is who a journal write names: a Claude session, a scheduled job, or
// the user at this host.
func Identity() string {
	job, session := os.Getenv("SLATE_ACTOR"), os.Getenv("CLAUDE_CODE_SESSION_ID")
	switch {
	case session != "" && job != "":
		return "claude-code-unattended/" + job + "/" + session
	case session != "" && os.Getenv("CLAUDE_CODE_SESSION_ATTENDED") == "0":
		return "claude-code-unattended/" + session
	case session != "":
		return "claude-code/" + session
	case job != "":
		return "worker/" + job
	}
	user := os.Getenv("USER")
	if user == "" {
		user = "unknown"
	}
	if host, _ := os.Hostname(); host != "" {
		return user + "@" + host
	}
	return user
}
