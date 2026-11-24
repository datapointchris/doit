// doit is what to attend to now. The Go binary runs each verb it has ported,
// and hands every other command line to doit-python unchanged.
package main

import (
	"errors"
	"fmt"
	"os"

	"github.com/datapointchris/doit/cli/internal/passthrough"
)

func main() {
	args := os.Args[1:]
	if owns(args) {
		os.Exit(execute())
	}
	err := passthrough.Exec(args, treeCommands(newRootCommand()))
	if errors.Is(err, passthrough.ErrNotInstalled) {
		verb := unportedVerb(args)
		if verb == "" {
			os.Exit(execute())
		}
		err = fmt.Errorf("%s is not installed, and %s is not yet in the Go doit", passthrough.Program, verb)
	}
	fmt.Fprintln(os.Stderr, "doit:", err)
	os.Exit(1)
}
