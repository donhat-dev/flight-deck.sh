package main

import (
	"fmt"
	"io"
	"runtime"
)

// version is set at build time: -ldflags "-X main.version=<git describe>".
var version = "dev"

func init() {
	commands["version"] = command{"print the build version", func(args []string) (output, error) {
		if err := parse(newFlags("version"), args); err != nil {
			return output{}, err
		}
		v := map[string]string{"version": version, "go": runtime.Version()}
		return output{Data: v, Text: func(w io.Writer) {
			fmt.Fprintf(w, "fd %s (%s)\n", version, runtime.Version())
		}}, nil
	}}
}
