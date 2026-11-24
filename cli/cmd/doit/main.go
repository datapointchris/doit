// doit is what to attend to now. The Go binary runs each verb it has ported,
// and hands every other command line to doit-python unchanged.
package main

import (
	"fmt"
	"os"

	"github.com/datapointchris/doit/cli/internal/passthrough"
)

func main() {
	args := os.Args[1:]
	if !owns(args) {
		err := passthrough.Exec(args)
		fmt.Fprintln(os.Stderr, "doit:", err)
		os.Exit(1)
	}
	os.Exit(execute())
}
