package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
	"text/tabwriter"

	"fdcore/internal/ledger"
	"fdcore/internal/pricing"
)

func init() {
	commands["pricing"] = command{"show or change the price table: list, set, import, defaults", cmdPricing}
}

func cmdPricing(args []string) (output, error) {
	if len(args) == 0 || strings.HasPrefix(args[0], "-") {
		return pricingList(args)
	}
	switch args[0] {
	case "list":
		return pricingList(args[1:])
	case "set":
		return pricingSet(args[1:])
	case "import":
		return pricingImport(args[1:])
	case "defaults":
		return pricingDefaults(args[1:])
	}
	return output{}, refuse("unknown pricing command %q: list, set, import or defaults", args[0])
}

func pricingList(args []string) (output, error) {
	fs := newFlags("pricing list")
	p := addPaths(fs)
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	c, err := ledger.Prices(s.DB)
	if err != nil {
		return output{}, err
	}
	return output{Data: c, Text: func(w io.Writer) { printPrices(w, c) }}, nil
}

// pricingSet takes the model prefix first: `fd pricing set <prefix> --input N --output N`.
func pricingSet(args []string) (output, error) {
	if len(args) == 0 || strings.HasPrefix(args[0], "-") {
		return output{}, refuse("usage: fd pricing set <model prefix> --input N --output N [flags]")
	}
	fs := newFlags("pricing set")
	p := addPaths(fs)
	in := fs.Float64("input", -1, "USD per million input tokens")
	out := fs.Float64("output", -1, "USD per million output tokens")
	from := fs.String("valid-from", "", "first UTC date (YYYY-MM-DD) the price applies to; empty = always")
	read := fs.Float64("cache-read-mult", pricing.DefaultCacheReadMult, "cache read price as a share of input")
	w5 := fs.Float64("write-5m-mult", pricing.DefaultWrite5mMult, "5-minute cache write price as a share of input")
	w1 := fs.Float64("write-1h-mult", pricing.DefaultWrite1hMult, "1-hour cache write price as a share of input")
	fastIn := fs.Float64("fast-input", -1, "fast-mode input price; leave out when the model has none")
	fastOut := fs.Float64("fast-output", -1, "fast-mode output price; leave out when the model has none")
	note := fs.String("note", "", "where the price came from")
	if err := parse(fs, args[1:]); err != nil {
		return output{}, err
	}
	if *in < 0 || *out < 0 {
		return output{}, refuse("--input and --output are required")
	}
	if (*fastIn >= 0) != (*fastOut >= 0) {
		return output{}, refuse("--fast-input and --fast-output go together")
	}
	row := pricing.Row{Prefix: args[0], ValidFrom: *from, Input: *in, Output: *out, CacheReadMult: *read,
		Write5mMult: *w5, Write1hMult: *w1, Note: *note}
	if *fastIn >= 0 {
		row.FastInput, row.FastOutput = fastIn, fastOut
	}
	if _, err := (pricing.Config{Models: []pricing.Row{row}}).Normalize(); err != nil {
		return output{}, refuse("%v", err)
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	tx, err := s.DB.Begin()
	if err != nil {
		return output{}, err
	}
	defer tx.Rollback()
	if err := ledger.SetPrice(tx, row); err != nil {
		return output{}, err
	}
	if err := tx.Commit(); err != nil {
		return output{}, err
	}
	return output{Data: map[string]any{"set": row}, Text: func(w io.Writer) {
		fmt.Fprintf(w, "%s: $%g input, $%g output per MTok, from %s\n", row.Prefix, row.Input, row.Output,
			orAlways(row.ValidFrom))
	}}, nil
}

// pricingImport replaces the whole table with a JSON file in the shape
// `fd pricing list --json` prints; `-` reads stdin.
func pricingImport(args []string) (output, error) {
	if len(args) == 0 || (strings.HasPrefix(args[0], "-") && args[0] != "-") {
		return output{}, refuse("usage: fd pricing import <file.json | ->")
	}
	fs := newFlags("pricing import")
	p := addPaths(fs)
	if err := parse(fs, args[1:]); err != nil {
		return output{}, err
	}
	var r io.Reader = os.Stdin
	if args[0] != "-" {
		f, err := os.Open(args[0])
		if err != nil {
			return output{}, refuse("%v", err)
		}
		defer f.Close()
		r = f
	}
	var c pricing.Config
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields() // a misspelt field must not silently price at 0
	if err := dec.Decode(&c); err != nil {
		return output{}, refuse("%s: %v", args[0], err)
	}
	n, err := c.Normalize()
	if err != nil {
		return output{}, refuse("%v", err)
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	tx, err := s.DB.Begin()
	if err != nil {
		return output{}, err
	}
	defer tx.Rollback()
	if err := ledger.ReplacePrices(tx, n); err != nil {
		return output{}, err
	}
	if err := tx.Commit(); err != nil {
		return output{}, err
	}
	return output{Data: map[string]any{"models": len(n.Models), "geo": len(n.Geo)}, Text: func(w io.Writer) {
		fmt.Fprintf(w, "price table replaced: %d model rows, %d geo multipliers\n", len(n.Models), len(n.Geo))
	}}, nil
}

func pricingDefaults(args []string) (output, error) {
	if err := parse(newFlags("pricing defaults"), args); err != nil {
		return output{}, err
	}
	c := pricing.Defaults()
	return output{Data: c, Text: func(w io.Writer) { printPrices(w, c) }}, nil
}

func printPrices(w io.Writer, c pricing.Config) {
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "prefix\tfrom\tinput\toutput\tcache read\tfast\tnote")
	for _, r := range c.Models {
		fast := "-"
		if r.FastInput != nil {
			fast = fmt.Sprintf("%g/%g", *r.FastInput, *r.FastOutput)
		}
		fmt.Fprintf(tw, "%s\t%s\t%g\t%g\t%gx\t%s\t%s\n", r.Prefix, orAlways(r.ValidFrom), r.Input, r.Output,
			r.CacheReadMult, fast, r.Note)
	}
	tw.Flush()
	geos := make([]string, 0, len(c.Geo))
	for g := range c.Geo {
		geos = append(geos, g)
	}
	sort.Strings(geos)
	for _, g := range geos {
		fmt.Fprintf(w, "inference_geo %s: x%g on every token class\n", g, c.Geo[g])
	}
}

func orAlways(date string) string {
	if date == "" {
		return "always"
	}
	return date
}
