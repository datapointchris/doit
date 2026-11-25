// doit is what to attend to now. The Go binary runs every command line, and
// `doit tui` hands its arguments to doit-python unchanged.
package main

import "os"

func main() {
	os.Exit(execute())
}
