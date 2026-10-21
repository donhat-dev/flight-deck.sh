// fd-runner keeps Claude Code processes alive for Odoo; the work lives in the
// runner package so its Main is testable. See go/runner/main.go.
package main

import (
	"os"

	"flightdeck/runner"
)

func main() {
	os.Exit(runner.Main(os.Args[1:]))
}
