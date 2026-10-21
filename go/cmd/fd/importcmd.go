package main

import (
	"errors"
	"fmt"
	"io"
	"os"

	"flightdeck/core/importer"
	"flightdeck/core/ledger"
)

func init() {
	commands["import"] = command{"import billed rows from an old FlightDeck ledger dump", cmdImport}
}

func cmdImport(args []string) (output, error) {
	fs := newFlags("import")
	p := addPaths(fs)
	src := fs.String("pg-copy", "", "`pg_restore -f - --data-only --table=messages` output; - reads stdin")
	dry := fs.Bool("dry-run", false, "report what would be imported and change nothing")
	noIngest := fs.Bool("no-ingest", false, "skip reading transcripts first")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if *src == "" {
		return output{}, refuse("--pg-copy is required")
	}
	var r io.Reader = os.Stdin
	if *src != "-" {
		f, err := os.Open(*src)
		if err != nil {
			return output{}, refuse("%v", err)
		}
		defer f.Close()
		r = f
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	// Transcripts first: a response a transcript still holds must come from it.
	if !*noIngest {
		if _, err := ingestAll(s, p.projects); err != nil {
			return output{}, err
		}
	}
	st, err := importer.PGCopy(s, r, *dry)
	if errors.Is(err, importer.ErrNoCopyHeader) {
		return output{}, refuse("%v", err)
	}
	if err != nil {
		return output{}, err
	}
	return output{Data: map[string]any{"dry_run": *dry, "stats": st}, Text: func(w io.Writer) {
		verb := "imported"
		if *dry {
			verb = "would import"
		}
		fmt.Fprintf(w, "%s %d of %d billed rows (%d already present, %d in sessions with transcripts)\n",
			verb, st.Inserted, st.Billed, st.KeyPresent, st.SessionOnDisk)
		fmt.Fprintf(w, "possible double counts: %d rows, $%.2f\n", st.OverlapRows, st.OverlapCost)
	}}, nil
}
