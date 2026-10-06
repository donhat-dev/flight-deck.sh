// fd is the FlightDeck core for one host: it reads Claude Code transcripts into a
// durable spend ledger and answers from it, as a CLI or as a daemon (`fd serve`).
//
// Output contract, kept from the Python `flightdeck` CLI: with --json, stdout
// carries exactly one JSON document. Exit 0 = ok, 2 = the command refused and
// said why in {"error": ...} on stdout, 3 = unknown command, 1 = crash (stderr).
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
)

// output is what a command returns: Data for --json, Text for a person.
// A nil Text prints Data as JSON either way.
type output struct {
	Data any
	Text func(w io.Writer)
}

type command struct {
	help string
	run  func(args []string) (output, error)
}

// commands is filled by each command file's init.
var commands = map[string]command{}

// refusal is an expected "no" (a bad flag, a missing directory): exit 2.
type refusal struct{ msg string }

func (r refusal) Error() string { return r.msg }

func refuse(format string, a ...any) error { return refusal{fmt.Sprintf(format, a...)} }

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) (code int) {
	asJSON := wantsJSON(args)
	defer func() {
		if p := recover(); p != nil {
			fmt.Fprintf(stderr, "fd: crash: %v\n", p)
			code = 1
		}
	}()
	if len(args) == 0 || args[0] == "help" || args[0] == "-h" || args[0] == "--help" {
		usage(stdout)
		return 0
	}
	cmd, ok := commands[args[0]]
	if !ok {
		return answer(stdout, stderr, asJSON, 3, fmt.Sprintf("unknown command %q; `fd help` lists them", args[0]))
	}
	out, err := cmd.run(args[1:])
	if err != nil {
		var h helpText
		if errors.As(err, &h) {
			fmt.Fprint(stdout, h.text)
			return 0
		}
		var r refusal
		if errors.As(err, &r) {
			return answer(stdout, stderr, asJSON, 2, r.msg)
		}
		fmt.Fprintf(stderr, "fd %s: %v\n", args[0], err)
		return 1
	}
	if asJSON || out.Text == nil {
		enc := json.NewEncoder(stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(out.Data); err != nil {
			fmt.Fprintf(stderr, "fd %s: %v\n", args[0], err)
			return 1
		}
		return 0
	}
	out.Text(stdout)
	return 0
}

// answer reports a refusal: as data on stdout with --json, as a line on stderr otherwise.
func answer(stdout, stderr io.Writer, asJSON bool, code int, msg string) int {
	if asJSON {
		json.NewEncoder(stdout).Encode(map[string]string{"error": msg})
	} else {
		fmt.Fprintf(stderr, "fd: %s\n", msg)
	}
	return code
}

func wantsJSON(args []string) bool {
	for _, a := range args {
		switch a {
		case "--json", "-json", "--json=true", "-json=true":
			return true
		}
	}
	return false
}

func usage(w io.Writer) {
	names := make([]string, 0, len(commands))
	for n := range commands {
		names = append(names, n)
	}
	sort.Strings(names)
	fmt.Fprintln(w, "usage: fd <command> [flags]   (--json prints one JSON document)")
	for _, n := range names {
		fmt.Fprintf(w, "  %-10s %s\n", n, commands[n].help)
	}
}

// newFlags is a command's flag set. Every command accepts --json; run reads it.
func newFlags(name string) *flag.FlagSet {
	fs := flag.NewFlagSet("fd "+name, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Bool("json", false, "print one JSON document on stdout")
	return fs
}

// helpText is a command's own help, asked for with -h or --help: exit 0.
type helpText struct{ text string }

func (h helpText) Error() string { return h.text }

func parse(fs *flag.FlagSet, args []string) error {
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			var b strings.Builder
			fmt.Fprintf(&b, "usage: %s [flags]\n", fs.Name())
			fs.SetOutput(&b)
			fs.PrintDefaults()
			return helpText{b.String()}
		}
		return refuse("%v", err)
	}
	if fs.NArg() > 0 {
		return refuse("unexpected argument %q", fs.Arg(0))
	}
	return nil
}
