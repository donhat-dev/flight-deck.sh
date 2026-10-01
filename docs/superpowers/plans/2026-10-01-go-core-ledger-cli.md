# Go Core Slice 1: Ledger CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `fd`, one static Go binary that reads Claude Code transcripts into a durable SQLite spend ledger and answers `spend`, `sessions` and the four spend API endpoints with the same numbers as the Python backend.

**Architecture:** `core/` is a Go module (`fdcore`) with five packages: `pricing` (the price table and cost math; prices are data, seeded from `defaults.json` and edited with `fd pricing`), `ledger` (SQLite schema, upsert, read cursor, stored prices), `ingest` (incremental transcript reader), `metrics` (the (day, model, session, speed, geo) grain and the four answers), `server` (HTTP API and file watcher). A sixth, `importer`, loads the 2026-09-23 Postgres dump. `cmd/fd` wires them into subcommands under the Python CLI's output contract. The Python backend is the parity oracle and is not changed.

**Tech Stack:** Go 1.26 (in `golang:1.26-alpine` via Docker), `modernc.org/sqlite` (pure Go, `CGO_ENABLED=0`), `github.com/fsnotify/fsnotify`, standard library. Python 3 for the parity scripts.

**Spec:** `docs/2026-10-01-go-core-binary.md`

## Global Constraints

- No Go on the host. Every Go command runs through `make go-tidy`, `make go-test` or `make go-build`, which run `golang:1.26-alpine` in Docker as the calling user with `CGO_ENABLED=0` and `GOFLAGS=-buildvcs=false` (the image has no git).
- Dependencies: `modernc.org/sqlite` and `github.com/fsnotify/fsnotify` only; everything else from the standard library. On 2026-10-01 `go mod tidy` resolved `modernc.org/sqlite v1.60.1` and `fsnotify v1.10.1` under go1.26.8, and the plan's code compiled with all tests passing: 51 before the price-table revision (5 repeated runs of the watcher and concurrency tests), 63 after it (3 repeated runs).
- Module `fdcore` at `flight-deck.sh/core/`; binary `core/bin/fd` (gitignored).
- CLI contract, kept from `backend/flightdeck/cli.py`: with `--json`, one JSON document on stdout and nothing else; exit 0 ok, 2 refusal with `{"error": …}` on stdout, 3 unknown command, 1 crash (message on stderr).
- Prices are a table, not code (owner's decision, 2026-10-01). They live in the ledger (`price_models`, `price_geo`), seeded once from the embedded `internal/pricing/defaults.json`, and change with `fd pricing set|import`. The defaults match `backend/flightdeck/pricing.py` (Anthropic's table re-read 2026-10-01, Sonnet 5.5 = $2/$10). A model with no row is unpriced. The cost expression keeps pricing.py's term order.
- JSON keys match the FastAPI responses exactly; an empty list marshals as `[]`, never `null`.
- Ranges use the local calendar date compared against UTC timestamps, as `runtime.since()` does.
- Defaults: ledger `~/.flightdeck/fd/ledger.db` (`FD_DB`), projects `$CLAUDE_CONFIG_DIR/projects` else `~/.claude/projects` (`FD_PROJECTS`), `fd serve` on `127.0.0.1:8011` (`FD_ADDR`), subscription price `FD_SUBSCRIPTION_USD` (default 0).
- Never touch the live Postgres ledger or `flightdeck.service`. `~/.claude/projects` is read-only for this work.
- Delete nothing. Temporary files go to the session scratch directory or `~/.cache/fd-parity/`, and each is listed in `/home/nathando/Documents/Projects/TEMP_FILES_SHOULD_BE_REMOVE.MD`.
- Code, comments and commit messages in English. Comments state constraints or reasons, not what the code does.
- Git: before Task 1, `git switch -c go-core-slice1` in `flight-deck.sh` (keeps the uncommitted backend changes in the working tree). Stage only the files a task names. Commit with `git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit`. Never push.

## Review Focus

1. **The ledger's cursor rows are wiped while transcripts are unchanged** (the 2026-09-29 failure): the next sweep must re-read every file. Pinned by `TestAWipedLedgerIsReadAgainInFull` in Task 4.
2. **A transcript is deleted after it was read** (Claude Code cleanup): its rows must stay. Pinned by `TestADeletedTranscriptKeepsItsRows` in Task 4.
3. **The CLI ingests while `fd serve` ingests** the same ledger: each response stored once, no "database is locked" failure. Pinned by `TestTwoReadersAtOnceStoreEachResponseOnce` in Task 4.
4. **A session directory appears after `fd serve` started** (a new session's `subagents/`): its files must be read without waiting for the 5-minute sweep. Pinned by `TestANewDirectoryIsWatched` in Task 7.
5. **A multi-megabyte line** (a large tool result): parsed, neither truncated nor fatal. Pinned by `TestALineLargerThanTheReadBufferIsParsed` in Task 4.

---

## File Structure

```
flight-deck.sh/
  Makefile                          + go-tidy, go-test, go-build targets
  core/
    .gitignore                      bin/
    go.mod, go.sum                  module fdcore
    cmd/fd/
      main.go                       dispatch, output contract, flag helpers
      version.go                    fd version
      flags.go                      shared paths/env defaults, ingestAll, validRange
      spend.go                      fd ingest | spend | sessions
      pricing.go                    fd pricing list | set | import | defaults
      serve.go                      fd serve
      importcmd.go                  fd import
      main_test.go, spend_test.go, pricing_test.go
    internal/pricing/pricing.go     Config, Row, Table: validation, rate choice, cost
    internal/pricing/defaults.json  the shipped price table
    internal/ledger/store.go        schema, Upsert, SetTitle, cursor
    internal/ledger/prices.go       stored prices: Prices, ReplacePrices, SetPrice, PriceTable
    internal/ingest/parse.go        transcript line -> ledger.Response
    internal/ingest/file.go         one file, incremental
    internal/ingest/sweep.go        the tree, oldest first
    internal/metrics/grain.go       Cell, Grain, Since
    internal/metrics/summary.go     Summarize
    internal/metrics/daily.go       Daily
    internal/metrics/bymodel.go     ByModel
    internal/metrics/sessions.go    Sessions
    internal/server/server.go       HTTP API, UI, ingest entry points
    internal/server/watch.go        fsnotify watcher and sweep ticker
    internal/importer/pgcopy.go     pg_restore COPY text -> ledger
  scripts/go-parity/
    python_metrics.py               oracle: Python metrics over a fresh SQLite ledger
    compare.py                      field-by-field diff, exit 1 on any difference
```

---

### Task 1: Module, build targets and the CLI contract

**Files:**
- Create: `core/go.mod`
- Create: `core/cmd/fd/main.go`
- Create: `core/cmd/fd/version.go`
- Test: `core/cmd/fd/main_test.go`
- Modify: `Makefile` (append the Go block)
- Create: `core/.gitignore` (`bin/`; the root `.gitignore` carries unrelated uncommitted edits)

**Interfaces:**
- Consumes: nothing.
- Produces (package `main`, used by every later command file):
  - `type output struct { Data any; Text func(w io.Writer) }`
  - `type command struct { help string; run func(args []string) (output, error) }`
  - `var commands map[string]command` (filled in each file's `init`)
  - `func refuse(format string, a ...any) error` (exit 2)
  - `func newFlags(name string) *flag.FlagSet` (accepts `--json`)
  - `func parse(fs *flag.FlagSet, args []string) error` (flag errors become refusals)
  - `func run(args []string, stdout, stderr io.Writer) int`
  - `var version string`

- [ ] **Step 1: Branch and add the build targets**

```bash
cd /home/nathando/Documents/Projects/flight-deck.sh
git switch -c go-core-slice1
```

Append to `Makefile`:

```make
# --- Go core (`fd`) ----------------------------------------------------------
# No Go on the host: build and test in the official image, as the calling user so
# files written into the tree stay theirs. Module and build caches persist under
# ~/.cache/fd-go. The image has no git, so VCS stamping is off.
GO_CACHE    := $(HOME)/.cache/fd-go
GO          := docker run --rm -u $(shell id -u):$(shell id -g) \
               -v $(PROJ):/src -v $(GO_CACHE):/cache -w /src/core \
               -e CGO_ENABLED=0 -e GOFLAGS=-buildvcs=false -e HOME=/cache \
               -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod \
               golang:1.26-alpine go
GOTEST_ARGS ?= ./...
FD_VERSION  := $(shell git describe --always --dirty 2>/dev/null || echo dev)

.PHONY: go-tidy go-test go-build
go-tidy:                    ## resolve the Go core's module requirements
	mkdir -p $(GO_CACHE)
	$(GO) mod tidy
go-test:                    ## run the Go core tests (GOTEST_ARGS narrows them)
	mkdir -p $(GO_CACHE)
	$(GO) test $(GOTEST_ARGS)
go-build:                   ## build core/bin/fd (static, linux/amd64)
	mkdir -p $(GO_CACHE)
	$(GO) build -trimpath -ldflags "-s -w -X main.version=$(FD_VERSION)" -o bin/fd ./cmd/fd
```

Create `core/.gitignore` containing the single line `bin/`. Create `core/go.mod`:

```
module fdcore

go 1.26
```

- [ ] **Step 2: Write the failing test**

`core/cmd/fd/main_test.go`:

```go
package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func runFd(args ...string) (int, string, string) {
	var out, errb bytes.Buffer
	code := run(args, &out, &errb)
	return code, out.String(), errb.String()
}

// oneDocument decodes stdout and fails if anything follows the first JSON value.
func oneDocument(t *testing.T, stdout string, v any) {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(stdout))
	if err := dec.Decode(v); err != nil {
		t.Fatalf("stdout is not JSON: %v\n%s", err, stdout)
	}
	if dec.More() {
		t.Fatalf("more than one JSON document on stdout:\n%s", stdout)
	}
}

func TestVersionPrintsOneJSONDocument(t *testing.T) {
	code, out, _ := runFd("version", "--json")
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	var v map[string]string
	oneDocument(t, out, &v)
	if v["version"] == "" || v["go"] == "" {
		t.Fatalf("got %v", v)
	}
}

func TestUnknownCommandExitsThreeWithTheErrorAsData(t *testing.T) {
	code, out, _ := runFd("nope", "--json")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 3 || v["error"] == "" {
		t.Fatalf("exit %d, %v", code, v)
	}
}

func TestABadFlagIsARefusal(t *testing.T) {
	code, out, _ := runFd("version", "--json", "--bogus")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || !strings.Contains(v["error"], "bogus") {
		t.Fatalf("exit %d, %v", code, v)
	}
}

func TestWithoutJSONARefusalGoesToStderr(t *testing.T) {
	code, out, errOut := runFd("version", "--bogus")
	if code != 2 || out != "" || !strings.Contains(errOut, "bogus") {
		t.Fatalf("exit %d stdout %q stderr %q", code, out, errOut)
	}
}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./cmd/fd"`
Expected: FAIL to build, `undefined: run`.

- [ ] **Step 4: Write the implementation**

`core/cmd/fd/main.go`:

```go
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

func parse(fs *flag.FlagSet, args []string) error {
	if err := fs.Parse(args); err != nil {
		return refuse("%v", err)
	}
	if fs.NArg() > 0 {
		return refuse("unexpected argument %q", fs.Arg(0))
	}
	return nil
}
```

`core/cmd/fd/version.go`:

```go
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
```

- [ ] **Step 5: Run the tests and build**

Run: `make go-test GOTEST_ARGS="./cmd/fd -v" && make go-build && core/bin/fd version`
Expected: 4 tests PASS; `fd <git describe> (go1.26.x)`.

- [ ] **Step 6: Commit**

```bash
git add Makefile core/.gitignore core/go.mod core/cmd/fd/main.go core/cmd/fd/version.go core/cmd/fd/main_test.go
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): fd binary skeleton with the CLI output contract"
```

---

### Task 2: Pricing as a table

Prices are data, not code (owner's decision, 2026-10-01). This package holds the
table's shape, its validation, the rate choice and the cost math; Task 3 stores
the table in the ledger and Task 6 adds `fd pricing` to edit it.

**Files:**
- Create: `core/internal/pricing/pricing.go`
- Create: `core/internal/pricing/defaults.json`
- Test: `core/internal/pricing/pricing_test.go`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type Row struct { Prefix, ValidFrom string; Input, Output, CacheReadMult, Write5mMult, Write1hMult float64; FastInput, FastOutput *float64; Note string }` (JSON keys `prefix`, `valid_from`, `input`, `output`, `cache_read_mult`, `write_5m_mult`, `write_1h_mult`, `fast_input`, `fast_output`, `note`)
  - `type Config struct { Models []Row; Geo map[string]float64 }` (JSON `models`, `geo`)
  - `func Defaults() Config`; `func (c Config) Normalize() (Config, error)`
  - `const DefaultCacheReadMult, DefaultWrite5mMult, DefaultWrite1hMult`
  - `type Table struct`; `func New(c Config) (*Table, error)`
  - `type Rate struct { In, Out float64 }`
  - `type Usage struct { Model string; Input, CacheRead, Create5m, Create1h, Out int64; TS, Speed, Geo string }`
  - `func (t *Table) RateFor(model, ts string) (Rate, bool)`
  - `func (t *Table) Cost(u Usage) (float64, bool)` (false = unpriced)
  - `func (t *Table) UncachedCost(u Usage) (float64, bool)`
  - `func (t *Table) CacheSavings(u Usage) float64`

- [ ] **Step 1: Write the failing test**

`core/internal/pricing/pricing_test.go`:

```go
package pricing

import (
	"math"
	"strings"
	"testing"
)

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Errorf("%s = %.12f, want %.12f", name, got, want)
	}
}

func defaults(t *testing.T) *Table {
	t.Helper()
	tbl, err := New(Defaults())
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func f(v float64) *float64 { return &v }

func TestTheDefaultsMatchThePublishedTable(t *testing.T) {
	c := Defaults()
	if len(c.Models) != 17 || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
	byPrefix := map[string]Row{}
	for _, r := range c.Models {
		byPrefix[r.Prefix] = r
	}
	if r := byPrefix["claude-opus-5-5"]; r.CacheReadMult != 0.05 || *r.FastInput != 8 || r.Write1hMult != 2 {
		t.Errorf("opus 5.5 %+v", r)
	}
	if r := byPrefix["claude-fable-5-1"]; r.CacheReadMult != 0.025 || r.FastInput != nil {
		t.Errorf("fable 5.1 %+v", r)
	}
	if r := byPrefix["claude-sonnet-5-5"]; r.Input != 2 || r.Output != 10 || r.CacheReadMult != 0.1 {
		t.Errorf("sonnet 5.5 %+v", r)
	}
}

func TestLongestPrefixWins(t *testing.T) {
	tbl := defaults(t)
	cases := map[string]Rate{
		"claude-opus-4-8":          {5, 25},
		"claude-opus-4-1-20250805": {15, 75},
		"claude-opus-4-20250514":   {15, 75},
		"claude-opus-5-5":          {4, 20},
		"claude-opus-5-20260101":   {5, 25},
		"claude-fable-5-1":         {10, 50},
		"claude-sonnet-5-5":        {2, 10},
	}
	for m, want := range cases {
		got, ok := tbl.RateFor(m, "")
		if !ok || got != want {
			t.Errorf("%s: got %v %v, want %v", m, got, ok, want)
		}
	}
}

func TestAnUnknownModelIsUnpricedNotGuessed(t *testing.T) {
	tbl := defaults(t)
	for _, m := range []string{"", "gpt-5.6-sol", "claude-sonnet-3-7", "<synthetic>"} {
		if _, ok := tbl.RateFor(m, ""); ok {
			t.Errorf("%q is priced", m)
		}
		if _, ok := tbl.Cost(Usage{Model: m, Input: 1000}); ok {
			t.Errorf("%q has a cost", m)
		}
		if s := tbl.CacheSavings(Usage{Model: m, CacheRead: 1000}); s != 0 {
			t.Errorf("%q saves %v", m, s)
		}
	}
}

func TestCacheReadMultipliers(t *testing.T) {
	tbl := defaults(t)
	// 1M cache-read tokens: Opus 5.5 $4 x 0.05, Fable 5.1 $10 x 0.025, Opus 5 $5 x 0.1.
	for m, want := range map[string]float64{"claude-opus-5-5": 0.20, "claude-fable-5-1": 0.25, "claude-opus-5": 0.50} {
		got, _ := tbl.Cost(Usage{Model: m, CacheRead: 1_000_000})
		near(t, m, got, want)
	}
}

func TestFastModeAndUSInference(t *testing.T) {
	tbl := defaults(t)
	got, _ := tbl.Cost(Usage{Model: "claude-opus-5-5", Input: 1_000_000, Out: 1_000_000, Speed: "fast"})
	near(t, "fast in+out", got, 8+40)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-5-5", CacheRead: 1_000_000, Speed: "fast"})
	near(t, "fast keeps the cache multiplier", got, 8*0.05)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-4-7", Input: 1_000_000, Speed: "fast"})
	near(t, "no fast rate falls back to standard", got, 5)
	got, _ = tbl.Cost(Usage{Model: "claude-opus-5-5", Input: 1_000_000, CacheRead: 1_000_000, Geo: "us"})
	near(t, "us on every class", got, (4+4*0.05)*1.1)
}

func TestWritesCostMoreSoSavingsCanBeNegative(t *testing.T) {
	tbl := defaults(t)
	// Opus 5: 1M 5-minute writes $6.25 and 1M 1-hour writes $10; without caching both are $5.
	u := Usage{Model: "claude-opus-5", Create5m: 1_000_000, Create1h: 1_000_000}
	c, _ := tbl.Cost(u)
	near(t, "cost", c, 16.25)
	near(t, "net savings", tbl.CacheSavings(u), 10-16.25)
}

func TestARowAppliesFromItsDateByTheMessage(t *testing.T) {
	tbl, err := New(Config{Models: []Row{
		{Prefix: "claude-sonnet-5", Input: 1, Output: 5},
		{Prefix: "claude-sonnet-5", ValidFrom: "2026-09-01", Input: 2, Output: 10},
		{Prefix: "claude-later-1", ValidFrom: "2026-09-01", Input: 3, Output: 15},
	}})
	if err != nil {
		t.Fatal(err)
	}
	before, _ := tbl.RateFor("claude-sonnet-5", "2026-08-31T23:59:59.999Z")
	after, _ := tbl.RateFor("claude-sonnet-5", "2026-09-01T00:00:00.000Z")
	undated, _ := tbl.RateFor("claude-sonnet-5", "")
	if before != (Rate{1, 5}) || after != (Rate{2, 10}) || undated != (Rate{2, 10}) {
		t.Fatalf("before %v after %v undated %v", before, after, undated)
	}
	if _, ok := tbl.RateFor("claude-later-1", "2026-08-15T00:00:00Z"); ok {
		t.Error("a model is priced before its first row applies")
	}
}

func TestNormalizeFillsThePublishedMultipliers(t *testing.T) {
	c, err := Config{Models: []Row{{Prefix: "claude-x", Input: 1, Output: 2}}}.Normalize()
	if err != nil {
		t.Fatal(err)
	}
	r := c.Models[0]
	if r.CacheReadMult != 0.1 || r.Write5mMult != 1.25 || r.Write1hMult != 2 {
		t.Fatalf("%+v", r)
	}
}

func TestNormalizeRefusesATableThatWouldPriceWrongly(t *testing.T) {
	cases := map[string]Config{
		"no prefix":         {Models: []Row{{Input: 1}}},
		"time in the date":  {Models: []Row{{Prefix: "a", ValidFrom: "2026-09-01T12:00"}}},
		"duplicate row":     {Models: []Row{{Prefix: "a"}, {Prefix: "a"}}},
		"negative price":    {Models: []Row{{Prefix: "a", Input: -1}}},
		"half a fast price": {Models: []Row{{Prefix: "a", FastInput: f(8)}}},
		"zero geo":          {Geo: map[string]float64{"us": 0}},
	}
	for name, c := range cases {
		if _, err := c.Normalize(); err == nil {
			t.Errorf("%s: accepted", name)
		} else if strings.TrimSpace(err.Error()) == "" {
			t.Errorf("%s: empty error", name)
		}
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/pricing"`
Expected: FAIL to build, `undefined: New`.

- [ ] **Step 3: Write the implementation**

`core/internal/pricing/pricing.go`:

```go
// Package pricing prices Claude usage from a price table, per 1,000,000 tokens.
//
// Prices are data: the table lives in the ledger, is seeded from defaults.json
// (Anthropic's table, https://platform.claude.com/docs/en/about-claude/pricing,
// read 2026-10-01) and changes with `fd pricing`. A model with no row is
// unpriced, never priced from a neighbour: an honest gap beats an invisible
// wrong cost.
package pricing

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"
)

//go:embed defaults.json
var defaultsJSON []byte

// Published multipliers of base input, used when a row leaves them out.
const (
	DefaultCacheReadMult = 0.1
	DefaultWrite5mMult   = 1.25
	DefaultWrite1hMult   = 2.0
)

// Row is one price. ValidFrom is "" (always) or a YYYY-MM-DD UTC date from
// which the row applies, judged by the message's own timestamp, so a price
// change restates nothing before it. It must be a bare date: the metrics grain
// is per UTC day and exact only while no rate changes inside a day.
type Row struct {
	Prefix        string   `json:"prefix"`
	ValidFrom     string   `json:"valid_from"`
	Input         float64  `json:"input"`
	Output        float64  `json:"output"`
	CacheReadMult float64  `json:"cache_read_mult"`
	Write5mMult   float64  `json:"write_5m_mult"`
	Write1hMult   float64  `json:"write_1h_mult"`
	FastInput     *float64 `json:"fast_input,omitempty"`
	FastOutput    *float64 `json:"fast_output,omitempty"`
	Note          string   `json:"note,omitempty"`
}

// Config is the whole table, in the shape `fd pricing list --json` prints and
// `fd pricing import` reads. Geo maps usage.inference_geo to a multiplier on
// every token class.
type Config struct {
	Models []Row              `json:"models"`
	Geo    map[string]float64 `json:"geo"`
}

// Defaults is the table fd ships with.
func Defaults() Config {
	var c Config
	if err := json.Unmarshal(defaultsJSON, &c); err != nil {
		panic(fmt.Sprintf("pricing: embedded defaults.json: %v", err))
	}
	n, err := c.Normalize()
	if err != nil {
		panic(fmt.Sprintf("pricing: embedded defaults.json: %v", err))
	}
	return n
}

// Normalize fills omitted multipliers with the published ones, orders the rows,
// and refuses a table that would price wrongly.
func (c Config) Normalize() (Config, error) {
	out := Config{Models: make([]Row, 0, len(c.Models)), Geo: map[string]float64{}}
	seen := map[string]bool{}
	for _, r := range c.Models {
		r.Prefix = strings.TrimSpace(r.Prefix)
		if r.Prefix == "" {
			return Config{}, fmt.Errorf("a price row has no prefix")
		}
		if r.ValidFrom != "" {
			if _, err := time.Parse(time.DateOnly, r.ValidFrom); err != nil {
				return Config{}, fmt.Errorf("%s: valid_from %q is not a YYYY-MM-DD date", r.Prefix, r.ValidFrom)
			}
		}
		key := r.Prefix + "@" + r.ValidFrom
		if seen[key] {
			return Config{}, fmt.Errorf("%s: two rows valid from %q", r.Prefix, r.ValidFrom)
		}
		seen[key] = true
		if r.CacheReadMult == 0 {
			r.CacheReadMult = DefaultCacheReadMult
		}
		if r.Write5mMult == 0 {
			r.Write5mMult = DefaultWrite5mMult
		}
		if r.Write1hMult == 0 {
			r.Write1hMult = DefaultWrite1hMult
		}
		if r.Input < 0 || r.Output < 0 || r.CacheReadMult < 0 || r.Write5mMult < 0 || r.Write1hMult < 0 {
			return Config{}, fmt.Errorf("%s: prices and multipliers cannot be negative", r.Prefix)
		}
		if (r.FastInput == nil) != (r.FastOutput == nil) {
			return Config{}, fmt.Errorf("%s: fast_input and fast_output go together", r.Prefix)
		}
		if r.FastInput != nil && (*r.FastInput < 0 || *r.FastOutput < 0) {
			return Config{}, fmt.Errorf("%s: fast prices cannot be negative", r.Prefix)
		}
		out.Models = append(out.Models, r)
	}
	for g, m := range c.Geo {
		if m <= 0 {
			return Config{}, fmt.Errorf("geo %q: the multiplier must be positive", g)
		}
		out.Geo[g] = m
	}
	sort.Slice(out.Models, func(i, j int) bool {
		if out.Models[i].Prefix != out.Models[j].Prefix {
			return out.Models[i].Prefix < out.Models[j].Prefix
		}
		return out.Models[i].ValidFrom < out.Models[j].ValidFrom
	})
	return out, nil
}

type Rate struct{ In, Out float64 }

// Table answers prices from a normalized Config.
type Table struct {
	rows map[string][]Row // per prefix, oldest ValidFrom first
	geo  map[string]float64
}

func New(c Config) (*Table, error) {
	n, err := c.Normalize()
	if err != nil {
		return nil, err
	}
	t := &Table{rows: map[string][]Row{}, geo: n.Geo}
	for _, r := range n.Models {
		t.rows[r.Prefix] = append(t.rows[r.Prefix], r)
	}
	return t, nil
}

// row picks the longest matching prefix ("claude-opus-4-8" must not price as
// "claude-opus-4"), then that prefix's latest row in effect at ts. An empty ts
// takes the latest row, so an undated message never gets an old or
// promotional price. No row in effect means unpriced, not a shorter prefix.
func (t *Table) row(model, ts string) (Row, bool) {
	best := ""
	for p := range t.rows {
		if strings.HasPrefix(model, p) && len(p) > len(best) {
			best = p
		}
	}
	if model == "" || best == "" {
		return Row{}, false
	}
	rs := t.rows[best]
	if ts == "" {
		return rs[len(rs)-1], true
	}
	for i := len(rs) - 1; i >= 0; i-- {
		if rs[i].ValidFrom <= ts {
			return rs[i], true
		}
	}
	return Row{}, false
}

// RateFor is the list (input, output) rate as of ts (an ISO timestamp).
func (t *Table) RateFor(model, ts string) (Rate, bool) {
	r, ok := t.row(model, ts)
	return Rate{r.Input, r.Output}, ok
}

// Usage is one priced unit: a response, or a grain cell that shares one rate.
type Usage struct {
	Model                                     string
	Input, CacheRead, Create5m, Create1h, Out int64
	TS, Speed, Geo                            string
}

func (t *Table) effective(u Usage) (Row, Rate, bool) {
	r, ok := t.row(u.Model, u.TS)
	if !ok {
		return Row{}, Rate{}, false
	}
	rate := Rate{r.Input, r.Output}
	if u.Speed == "fast" && r.FastInput != nil {
		rate = Rate{*r.FastInput, *r.FastOutput}
	}
	m := 1.0
	if g, ok := t.geo[u.Geo]; ok {
		m = g
	}
	return r, Rate{rate.In * m, rate.Out * m}, true
}

// Cost is the API-equivalent USD cost; false when the model is unpriced. Terms
// are added in pricing.py's order so float sums agree with the oracle.
func (t *Table) Cost(u Usage) (float64, bool) {
	r, rate, ok := t.effective(u)
	if !ok {
		return 0, false
	}
	return (float64(u.Input)*rate.In +
		float64(u.Out)*rate.Out +
		float64(u.CacheRead)*r.CacheReadMult*rate.In +
		float64(u.Create5m)*r.Write5mMult*rate.In +
		float64(u.Create1h)*r.Write1hMult*rate.In) / 1_000_000, true
}

// UncachedCost is what the same tokens cost with no prompt caching.
func (t *Table) UncachedCost(u Usage) (float64, bool) {
	_, rate, ok := t.effective(u)
	if !ok {
		return 0, false
	}
	context := u.Input + u.CacheRead + u.Create5m + u.Create1h
	return (float64(context)*rate.In + float64(u.Out)*rate.Out) / 1_000_000, true
}

// CacheSavings is uncached minus cached cost, net of the write premium; 0 when unpriced.
func (t *Table) CacheSavings(u Usage) float64 {
	c, ok := t.Cost(u)
	if !ok {
		return 0
	}
	un, _ := t.UncachedCost(u)
	return un - c
}
```

`core/internal/pricing/defaults.json` (omitted multipliers take the published 0.1 / 1.25 / 2.0):

```json
{
  "models": [
    {"prefix": "claude-fable-5-1", "input": 10, "output": 50, "cache_read_mult": 0.025},
    {"prefix": "claude-mythos-5-1", "input": 10, "output": 50, "cache_read_mult": 0.025},
    {"prefix": "claude-fable-5", "input": 10, "output": 50},
    {"prefix": "claude-mythos-5", "input": 10, "output": 50},
    {"prefix": "claude-opus-5-5", "input": 4, "output": 20, "cache_read_mult": 0.05, "fast_input": 8, "fast_output": 40},
    {"prefix": "claude-opus-5", "input": 5, "output": 25, "fast_input": 10, "fast_output": 50},
    {"prefix": "claude-opus-4-8", "input": 5, "output": 25, "fast_input": 10, "fast_output": 50},
    {"prefix": "claude-opus-4-7", "input": 5, "output": 25},
    {"prefix": "claude-opus-4-6", "input": 5, "output": 25},
    {"prefix": "claude-opus-4-5", "input": 5, "output": 25},
    {"prefix": "claude-opus-4-1", "input": 15, "output": 75},
    {"prefix": "claude-opus-4", "input": 15, "output": 75},
    {"prefix": "claude-sonnet-5-5", "input": 2, "output": 10},
    {"prefix": "claude-sonnet-5", "input": 2, "output": 10},
    {"prefix": "claude-sonnet-4-6", "input": 3, "output": 15},
    {"prefix": "claude-sonnet-4-5", "input": 3, "output": 15},
    {"prefix": "claude-haiku-4-5", "input": 1, "output": 5}
  ],
  "geo": {"us": 1.1}
}
```

- [ ] **Step 4: Run the tests**

Run: `make go-test GOTEST_ARGS="./internal/pricing -v"`
Expected: 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/internal/pricing
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): price table with dated rows and published defaults"
```

---

### Task 3: Ledger store

**Files:**
- Create: `core/internal/ledger/store.go`
- Create: `core/internal/ledger/prices.go`
- Test: `core/internal/ledger/store_test.go`
- Test: `core/internal/ledger/prices_test.go`
- Modify: `core/go.mod`, `core/go.sum` (via `make go-tidy`)

**Interfaces:**
- Consumes: `pricing.Config`, `pricing.Row`, `pricing.Defaults`, `pricing.New`, `pricing.Table` (Task 2).
- Produces:
  - `func Prices(q Querier) (pricing.Config, error)`; `func PriceTable(q Querier) (*pricing.Table, error)`
  - `func ReplacePrices(tx *sql.Tx, c pricing.Config) error`; `func SetPrice(tx *sql.Tx, r pricing.Row) error`
  - Tables `price_models`, `price_geo`; `Open` seeds them from `pricing.Defaults()` once per ledger (meta key `prices_seeded`).
  - `type Store struct { DB *sql.DB }`; `func Open(path string) (*Store, error)`; `func (s *Store) Close() error`
  - `type Response struct { MessageID, RequestID, UUID, SessionID, Project, Model, TS string; Input, CacheRead, Create5m, Create1h, Output int64; ServiceTier, Speed, Geo string }`
  - `type Outcome int` with `New`, `Repeat`, `Copy`
  - `func Upsert(tx *sql.Tx, r Response) (Outcome, error)`
  - `func SetTitle(tx *sql.Tx, sessionID, title, source string) error` (`source` is `"custom"` or `"ai"`)
  - `type Cursor struct { Path string; MtimeNS, Size, BytesRead, Lines int64 }`
  - `func GetCursor(q Querier, path string) (Cursor, bool, error)`; `func PutCursor(tx *sql.Tx, c Cursor) error`
  - `type Querier interface { QueryRow(query string, args ...any) *sql.Row; Query(query string, args ...any) (*sql.Rows, error) }` (satisfied by `*sql.DB` and `*sql.Tx`)
  - Tables `responses`, `files`, `session_meta`, `meta`. Column names are the contract the metrics and importer SQL use.

- [ ] **Step 1: Write the failing test**

`core/internal/ledger/store_test.go`:

```go
package ledger

import (
	"path/filepath"
	"testing"
)

func open(t *testing.T) (*Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "fd", "ledger.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	return s, path
}

func upsert(t *testing.T, s *Store, r Response) Outcome {
	t.Helper()
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	o, err := Upsert(tx, r)
	if err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	return o
}

func TestOneResponseOverThreeLinesIsOneRowWithTheLargestOutput(t *testing.T) {
	s, _ := open(t)
	base := Response{MessageID: "M1", RequestID: "R1", SessionID: "s1", Model: "claude-opus-5-5",
		TS: "2026-09-25T07:00:00.000Z", Input: 2, CacheRead: 1000, Output: 10}
	l1, l2, l3 := base, base, base
	l1.UUID, l2.UUID, l3.UUID = "u1", "u2", "u3"
	l3.Output = 300
	got := []Outcome{upsert(t, s, l1), upsert(t, s, l2), upsert(t, s, l3)}
	if got[0] != New || got[1] != Repeat || got[2] != Repeat {
		t.Fatalf("outcomes %v", got)
	}
	var n, out int64
	s.DB.QueryRow(`SELECT count(*), max(output_tokens) FROM responses`).Scan(&n, &out)
	if n != 1 || out != 300 {
		t.Fatalf("rows %d output %d", n, out)
	}
}

func TestALaterSmallerOutputDoesNotLowerIt(t *testing.T) {
	s, _ := open(t)
	upsert(t, s, Response{MessageID: "M1", RequestID: "R1", UUID: "u1", SessionID: "s1", Output: 300})
	upsert(t, s, Response{MessageID: "M1", RequestID: "R1", UUID: "u2", SessionID: "s1", Output: 10})
	var out int64
	s.DB.QueryRow(`SELECT output_tokens FROM responses`).Scan(&out)
	if out != 300 {
		t.Fatalf("output %d", out)
	}
}

func TestAResumedCopyStaysWithTheFirstSession(t *testing.T) {
	s, _ := open(t)
	orig := Response{MessageID: "M1", RequestID: "R1", UUID: "u1", SessionID: "s1", Output: 5}
	cp := orig
	cp.UUID, cp.SessionID = "u9", "s2"
	upsert(t, s, orig)
	if o := upsert(t, s, cp); o != Copy {
		t.Fatalf("outcome %v", o)
	}
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses`).Scan(&sid)
	if sid != "s1" {
		t.Fatalf("billed to %s", sid)
	}
}

func TestEmptyStringsAreStoredAsNull(t *testing.T) {
	s, _ := open(t)
	upsert(t, s, Response{MessageID: "M1", UUID: "u1"})
	var nulls int
	s.DB.QueryRow(`SELECT (session_id IS NULL) + (ts IS NULL) + (model IS NULL) + (speed IS NULL) FROM responses`).Scan(&nulls)
	if nulls != 4 {
		t.Fatalf("%d of 4 columns are NULL", nulls)
	}
}

func TestACustomTitleWinsWhateverTheOrder(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	if err := SetTitle(tx, "s1", "Mine", "custom"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s1", "Generated", "ai"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s2", "Generated", "ai"); err != nil {
		t.Fatal(err)
	}
	if err := SetTitle(tx, "s2", "Renamed", "custom"); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	for sid, want := range map[string]string{"s1": "Mine", "s2": "Renamed"} {
		var title string
		s.DB.QueryRow(`SELECT title FROM session_meta WHERE session_id=?`, sid).Scan(&title)
		if title != want {
			t.Errorf("%s title %q, want %q", sid, title, want)
		}
	}
}

func TestTheCursorRoundTripsAndSurvivesReopen(t *testing.T) {
	s, path := open(t)
	if _, seen, err := GetCursor(s.DB, "/a.jsonl"); err != nil || seen {
		t.Fatalf("seen %v err %v", seen, err)
	}
	tx, _ := s.DB.Begin()
	if err := PutCursor(tx, Cursor{Path: "/a.jsonl", MtimeNS: 7, Size: 100, BytesRead: 90, Lines: 3}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	s.Close()
	s2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	c, seen, err := GetCursor(s2.DB, "/a.jsonl")
	if err != nil || !seen || c.BytesRead != 90 || c.Lines != 3 || c.MtimeNS != 7 {
		t.Fatalf("cursor %+v seen %v err %v", c, seen, err)
	}
}
```

`core/internal/ledger/prices_test.go`:

```go
package ledger

import (
	"testing"

	"fdcore/internal/pricing"
)

func prices(t *testing.T, s *Store) pricing.Config {
	t.Helper()
	c, err := Prices(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestANewLedgerHoldsTheShippedPrices(t *testing.T) {
	s, _ := open(t)
	got, want := prices(t, s), pricing.Defaults()
	if len(got.Models) != len(want.Models) || got.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(got.Models), got.Geo)
	}
	for i := range want.Models {
		g, w := got.Models[i], want.Models[i]
		if g.Prefix != w.Prefix || g.Input != w.Input || g.CacheReadMult != w.CacheReadMult ||
			(g.FastInput == nil) != (w.FastInput == nil) {
			t.Errorf("row %d: %+v, want %+v", i, g, w)
		}
	}
	tbl, err := PriceTable(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	if r, ok := tbl.RateFor("claude-opus-5-5", ""); !ok || r != (pricing.Rate{In: 4, Out: 20}) {
		t.Fatalf("opus 5.5 %v %v", r, ok)
	}
}

func TestSetPriceAddsOrReplacesOneRow(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	if err := SetPrice(tx, pricing.Row{Prefix: "claude-opus-5-5", Input: 8, Output: 40}); err != nil {
		t.Fatal(err)
	}
	if err := SetPrice(tx, pricing.Row{Prefix: "claude-new-1", ValidFrom: "2026-10-01", Input: 1, Output: 2}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	tbl, _ := PriceTable(s.DB)
	if r, _ := tbl.RateFor("claude-opus-5-5", ""); r != (pricing.Rate{In: 8, Out: 40}) {
		t.Errorf("opus 5.5 %v", r)
	}
	if n := len(prices(t, s).Models); n != len(pricing.Defaults().Models)+1 {
		t.Errorf("%d rows", n)
	}
}

func TestSetPriceRefusesABadRowAndKeepsTheTable(t *testing.T) {
	s, _ := open(t)
	tx, _ := s.DB.Begin()
	err := SetPrice(tx, pricing.Row{Prefix: "claude-x", ValidFrom: "2026-10-01T12:00", Input: 1})
	tx.Rollback()
	if err == nil {
		t.Fatal("a valid_from with a time was accepted")
	}
	if n := len(prices(t, s).Models); n != len(pricing.Defaults().Models) {
		t.Errorf("%d rows after a refused set", n)
	}
}

func TestAnEmptiedTableIsNotSeededAgain(t *testing.T) {
	s, path := open(t)
	tx, _ := s.DB.Begin()
	if err := ReplacePrices(tx, pricing.Config{}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	s.Close()
	s2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	if n := len(prices(t, s2).Models); n != 0 {
		t.Fatalf("reseeded: %d rows", n)
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/ledger"`
Expected: FAIL to build, `undefined: Open`.

- [ ] **Step 3: Write the implementation**

`core/internal/ledger/store.go`:

```go
// Package ledger is the durable spend ledger: one row per billed API response.
//
// It is owned data, not a cache. Claude Code deletes old transcripts, and twice
// (2026-08-25, 2026-09-29) Postgres crash recovery emptied the Python ledger's
// UNLOGGED tables; rows whose transcripts were already gone never came back. So
// a re-read never deletes a row, and the per-file read cursor lives in this file
// and commits in the same transaction as the rows it covers.
package ledger

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

const schemaVersion = "1"

const schema = `
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS responses (
  message_id      TEXT NOT NULL,
  request_id      TEXT NOT NULL,
  uuid            TEXT NOT NULL,
  session_id      TEXT,
  project         TEXT,
  model           TEXT,
  ts              TEXT,
  input_tokens    INTEGER NOT NULL DEFAULT 0,
  cache_read      INTEGER NOT NULL DEFAULT 0,
  cache_create_5m INTEGER NOT NULL DEFAULT 0,
  cache_create_1h INTEGER NOT NULL DEFAULT 0,
  output_tokens   INTEGER NOT NULL DEFAULT 0,
  service_tier    TEXT,
  speed           TEXT,
  inference_geo   TEXT,
  source          TEXT NOT NULL DEFAULT 'transcript',
  dedupe_method   TEXT,
  PRIMARY KEY (message_id, request_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS responses_ts ON responses(ts);
CREATE INDEX IF NOT EXISTS responses_session ON responses(session_id);
CREATE TABLE IF NOT EXISTS files (
  path       TEXT PRIMARY KEY,
  mtime_ns   INTEGER NOT NULL,
  size       INTEGER NOT NULL,
  bytes_read INTEGER NOT NULL,
  lines      INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS session_meta (
  session_id   TEXT PRIMARY KEY,
  title        TEXT,
  title_source TEXT
) WITHOUT ROWID;
`

type Store struct{ DB *sql.DB }

// Open creates or opens the ledger. WAL lets readers run beside the writer;
// _txlock=immediate takes the write lock at BEGIN, so two writers (the CLI and
// `fd serve`) queue on busy_timeout instead of failing on a lock upgrade.
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	dsn := "file:" + path + "?_pragma=journal_mode(WAL)&_pragma=synchronous(NORMAL)" +
		"&_pragma=busy_timeout(10000)&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec(schema + pricesSchema); err != nil {
		db.Close()
		return nil, fmt.Errorf("ledger schema: %w", err)
	}
	if _, err := db.Exec(`INSERT INTO meta (key, value) VALUES ('schema_version', ?)
		ON CONFLICT(key) DO NOTHING`, schemaVersion); err != nil {
		db.Close()
		return nil, err
	}
	if err := seedPrices(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("seed prices: %w", err)
	}
	return &Store{DB: db}, nil
}

func (s *Store) Close() error { return s.DB.Close() }

// Response is one billed API response. MessageID is message.id, or
// "uuid:<line uuid>" for a line written before message.id existed.
type Response struct {
	MessageID, RequestID, UUID                   string
	SessionID, Project, Model, TS                string
	Input, CacheRead, Create5m, Create1h, Output int64
	ServiceTier, Speed, Geo                      string
}

type Outcome int

const (
	New    Outcome = iota // the first line of a response: a new billed row
	Repeat                // a further line of a response this session already holds
	Copy                  // the response belongs to another session (resume or fork history)
)

// Upsert stores one billed line. The first session to bring a response keeps
// it; a later line may raise output_tokens (early lines carry a partial count).
func Upsert(tx *sql.Tx, r Response) (Outcome, error) {
	var session sql.NullString
	err := tx.QueryRow(`SELECT session_id FROM responses WHERE message_id=? AND request_id=?`,
		r.MessageID, r.RequestID).Scan(&session)
	if errors.Is(err, sql.ErrNoRows) {
		_, err = tx.Exec(`INSERT INTO responses (message_id, request_id, uuid, session_id, project,
			model, ts, input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens,
			service_tier, speed, inference_geo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
			r.MessageID, r.RequestID, r.UUID, null(r.SessionID), null(r.Project), null(r.Model),
			null(r.TS), r.Input, r.CacheRead, r.Create5m, r.Create1h, r.Output,
			null(r.ServiceTier), null(r.Speed), null(r.Geo))
		return New, err
	}
	if err != nil {
		return 0, err
	}
	if _, err := tx.Exec(`UPDATE responses SET output_tokens=? WHERE message_id=? AND request_id=?
		AND output_tokens<?`, r.Output, r.MessageID, r.RequestID, r.Output); err != nil {
		return 0, err
	}
	if session.String != r.SessionID {
		return Copy, nil
	}
	return Repeat, nil
}

// null stores "" as NULL, as the Python ledger stores a missing field.
func null(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// SetTitle records a session title. A user-set ("custom") title wins over the
// generated ("ai") one whatever order the records arrive in.
func SetTitle(tx *sql.Tx, sessionID, title, source string) error {
	q := `INSERT INTO session_meta (session_id, title, title_source) VALUES (?,?,?)
		ON CONFLICT(session_id) DO UPDATE SET title=excluded.title, title_source=excluded.title_source`
	if source == "ai" {
		q += ` WHERE session_meta.title_source IS NOT 'custom'`
	}
	_, err := tx.Exec(q, sessionID, title, source)
	return err
}

// Cursor is how far one transcript has been read.
type Cursor struct {
	Path                            string
	MtimeNS, Size, BytesRead, Lines int64
}

// Querier is satisfied by *sql.DB and *sql.Tx.
type Querier interface {
	QueryRow(query string, args ...any) *sql.Row
	Query(query string, args ...any) (*sql.Rows, error)
}

func GetCursor(q Querier, path string) (Cursor, bool, error) {
	c := Cursor{Path: path}
	err := q.QueryRow(`SELECT mtime_ns, size, bytes_read, lines FROM files WHERE path=?`, path).
		Scan(&c.MtimeNS, &c.Size, &c.BytesRead, &c.Lines)
	if errors.Is(err, sql.ErrNoRows) {
		return c, false, nil
	}
	return c, err == nil, err
}

func PutCursor(tx *sql.Tx, c Cursor) error {
	_, err := tx.Exec(`INSERT INTO files (path, mtime_ns, size, bytes_read, lines) VALUES (?,?,?,?,?)
		ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size,
		bytes_read=excluded.bytes_read, lines=excluded.lines`,
		c.Path, c.MtimeNS, c.Size, c.BytesRead, c.Lines)
	return err
}
```

`core/internal/ledger/prices.go`:

```go
package ledger

import (
	"database/sql"
	"time"

	"fdcore/internal/pricing"
)

const pricesSchema = `
CREATE TABLE IF NOT EXISTS price_models (
  prefix          TEXT NOT NULL,
  valid_from      TEXT NOT NULL DEFAULT '',
  input           REAL NOT NULL,
  output          REAL NOT NULL,
  cache_read_mult REAL NOT NULL,
  write_5m_mult   REAL NOT NULL,
  write_1h_mult   REAL NOT NULL,
  fast_input      REAL,
  fast_output     REAL,
  note            TEXT,
  PRIMARY KEY (prefix, valid_from)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS price_geo (geo TEXT PRIMARY KEY, mult REAL NOT NULL) WITHOUT ROWID;
`

// seedPrices writes the shipped table once per ledger. The meta flag, not an
// empty table, decides: an owner who imports an empty table keeps it empty.
func seedPrices(db *sql.DB) error {
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var n int
	if err := tx.QueryRow(`SELECT count(*) FROM meta WHERE key='prices_seeded'`).Scan(&n); err != nil {
		return err
	}
	if n > 0 {
		return nil
	}
	if err := ReplacePrices(tx, pricing.Defaults()); err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO meta (key, value) VALUES ('prices_seeded', ?)`,
		time.Now().UTC().Format(time.RFC3339)); err != nil {
		return err
	}
	return tx.Commit()
}

// Prices reads the stored table.
func Prices(q Querier) (pricing.Config, error) {
	c := pricing.Config{Models: []pricing.Row{}, Geo: map[string]float64{}}
	rows, err := q.Query(`SELECT prefix, valid_from, input, output, cache_read_mult, write_5m_mult,
		write_1h_mult, fast_input, fast_output, COALESCE(note,'') FROM price_models ORDER BY prefix, valid_from`)
	if err != nil {
		return c, err
	}
	defer rows.Close()
	for rows.Next() {
		var r pricing.Row
		var fi, fo sql.NullFloat64
		if err := rows.Scan(&r.Prefix, &r.ValidFrom, &r.Input, &r.Output, &r.CacheReadMult,
			&r.Write5mMult, &r.Write1hMult, &fi, &fo, &r.Note); err != nil {
			return c, err
		}
		if fi.Valid && fo.Valid {
			r.FastInput, r.FastOutput = &fi.Float64, &fo.Float64
		}
		c.Models = append(c.Models, r)
	}
	if err := rows.Err(); err != nil {
		return c, err
	}
	geo, err := q.Query(`SELECT geo, mult FROM price_geo`)
	if err != nil {
		return c, err
	}
	defer geo.Close()
	for geo.Next() {
		var g string
		var m float64
		if err := geo.Scan(&g, &m); err != nil {
			return c, err
		}
		c.Geo[g] = m
	}
	return c, geo.Err()
}

// PriceTable is the stored table, ready to price usage.
func PriceTable(q Querier) (*pricing.Table, error) {
	c, err := Prices(q)
	if err != nil {
		return nil, err
	}
	return pricing.New(c)
}

// ReplacePrices swaps the whole table for c, after checking it.
func ReplacePrices(tx *sql.Tx, c pricing.Config) error {
	n, err := c.Normalize()
	if err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM price_models`); err != nil {
		return err
	}
	if _, err := tx.Exec(`DELETE FROM price_geo`); err != nil {
		return err
	}
	for _, r := range n.Models {
		if _, err := tx.Exec(`INSERT INTO price_models (prefix, valid_from, input, output, cache_read_mult,
			write_5m_mult, write_1h_mult, fast_input, fast_output, note) VALUES (?,?,?,?,?,?,?,?,?,?)`,
			r.Prefix, r.ValidFrom, r.Input, r.Output, r.CacheReadMult, r.Write5mMult, r.Write1hMult,
			r.FastInput, r.FastOutput, null(r.Note)); err != nil {
			return err
		}
	}
	for g, m := range n.Geo {
		if _, err := tx.Exec(`INSERT INTO price_geo (geo, mult) VALUES (?,?)`, g, m); err != nil {
			return err
		}
	}
	return nil
}

// SetPrice adds or replaces the row with r's prefix and valid_from, checking
// the resulting table as a whole.
func SetPrice(tx *sql.Tx, r pricing.Row) error {
	c, err := Prices(tx)
	if err != nil {
		return err
	}
	kept := []pricing.Row{}
	for _, x := range c.Models {
		if x.Prefix != r.Prefix || x.ValidFrom != r.ValidFrom {
			kept = append(kept, x)
		}
	}
	c.Models = append(kept, r)
	return ReplacePrices(tx, c)
}
```

- [ ] **Step 4: Resolve the dependency and run the tests**

Run: `make go-tidy && make go-test GOTEST_ARGS="./internal/ledger -v"`
Expected: `go.mod` gains `modernc.org/sqlite`; 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/go.mod core/go.sum core/internal/ledger
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): durable SQLite ledger keyed by (message_id, request_id), with stored prices"
```

---

### Task 4: Ingest

**Files:**
- Create: `core/internal/ingest/parse.go`
- Create: `core/internal/ingest/file.go`
- Create: `core/internal/ingest/sweep.go`
- Test: `core/internal/ingest/ingest_test.go`

**Interfaces:**
- Consumes: `ledger.Store`, `ledger.Response`, `ledger.Upsert`, `ledger.New`, `ledger.SetTitle`, `ledger.GetCursor`, `ledger.PutCursor`, `ledger.Cursor` (Task 3).
- Produces:
  - `type Ingester struct { Store *ledger.Store }`
  - `func (in *Ingester) File(path string) (int, error)` (new billed rows)
  - `func (in *Ingester) Sweep(root string) (Stats, error)`
  - `type Stats struct { Files, NewRows int }`
  - `func List(root string) ([]Transcript, error)`; `type Transcript struct { Path string; MtimeNS int64 }`

- [ ] **Step 1: Write the failing test**

`core/internal/ingest/ingest_test.go`:

```go
package ingest

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"fdcore/internal/ledger"
)

func asst(uuid, session, msg string, out int) string {
	return fmt.Sprintf(`{"type":"assistant","uuid":%q,"sessionId":%q,"cwd":"/p",`+
		`"timestamp":"2026-09-25T07:00:00.000Z","requestId":"req-%s","message":{"id":%q,`+
		`"model":"claude-opus-5-5","usage":{"input_tokens":2,"cache_read_input_tokens":1000,`+
		`"output_tokens":%d,"cache_creation":{"ephemeral_5m_input_tokens":0,`+
		`"ephemeral_1h_input_tokens":0}}}}`+"\n", uuid, session, msg, msg, out)
}

func setup(t *testing.T) (*ledger.Store, *Ingester, string) {
	t.Helper()
	dir := t.TempDir()
	s, err := ledger.Open(filepath.Join(dir, "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	root := filepath.Join(dir, "projects")
	if err := os.MkdirAll(filepath.Join(root, "-p"), 0o755); err != nil {
		t.Fatal(err)
	}
	return s, &Ingester{Store: s}, root
}

func write(t *testing.T, path string, lines ...string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, l := range lines {
		if _, err := f.WriteString(l); err != nil {
			t.Fatal(err)
		}
	}
}

func rows(t *testing.T, s *ledger.Store) int {
	t.Helper()
	var n int
	if err := s.DB.QueryRow(`SELECT count(*) FROM responses`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func mustFile(t *testing.T, in *Ingester, path string) int {
	t.Helper()
	n, err := in.File(path)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func mustSweep(t *testing.T, in *Ingester, root string) Stats {
	t.Helper()
	st, err := in.Sweep(root)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func TestThreeLinesOfOneResponseAreOneRowWithTheLargestOutput(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 10), asst("u2", "s1", "M1", 10), asst("u3", "s1", "M1", 300))
	if n := mustFile(t, in, f); n != 1 {
		t.Fatalf("new rows %d", n)
	}
	var out int
	s.DB.QueryRow(`SELECT output_tokens FROM responses`).Scan(&out)
	if rows(t, s) != 1 || out != 300 {
		t.Fatalf("rows %d output %d", rows(t, s), out)
	}
}

func TestOnlyAssistantLinesWithUsageAndAUUIDAreBilled(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f,
		`{"type":"user","uuid":"x1","sessionId":"s1","message":{"role":"user","content":"hi"}}`+"\n",
		`{"type":"assistant","uuid":"x2","sessionId":"s1","message":{"id":"M2","model":"claude-opus-5-5","usage":{}}}`+"\n",
		`{"type":"assistant","sessionId":"s1","message":{"id":"M3","model":"claude-opus-5-5","usage":{"output_tokens":1}}}`+"\n",
		`{"type":"assistant","uuid":"x4","sessionId":"s1","message":{"model":"claude-opus-4-1","usage":{"output_tokens":1}}}`+"\n")
	mustFile(t, in, f)
	var id string
	s.DB.QueryRow(`SELECT message_id FROM responses`).Scan(&id)
	if rows(t, s) != 1 || id != "uuid:x4" {
		t.Fatalf("rows %d id %q", rows(t, s), id)
	}
}

func TestTitlesAreRecorded(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, `{"type":"custom-title","sessionId":"s1","customTitle":"Mine"}`+"\n",
		`{"type":"ai-title","sessionId":"s1","aiTitle":"Generated"}`+"\n")
	mustFile(t, in, f)
	var title string
	s.DB.QueryRow(`SELECT title FROM session_meta WHERE session_id='s1'`).Scan(&title)
	if title != "Mine" {
		t.Fatalf("title %q", title)
	}
}

func TestATrailingLineWithoutNewlineWaitsForTheNextPass(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), strings.TrimSuffix(asst("u2", "s1", "M2", 5), "\n"))
	mustFile(t, in, f)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d before the newline", n)
	}
	write(t, f, "\n")
	mustFile(t, in, f)
	if n := rows(t, s); n != 2 {
		t.Fatalf("rows %d after the newline", n)
	}
}

func TestANulRunIsSkippedNotFatal(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), strings.Repeat("\x00", 64)+"\n", asst("u2", "s1", "M2", 5))
	mustFile(t, in, f)
	if n := rows(t, s); n != 2 {
		t.Fatalf("rows %d", n)
	}
}

func TestARewrittenShorterFileIsReadFromTheTop(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5), asst("u2", "s1", "M2", 5), asst("u3", "s1", "M3", 5))
	mustFile(t, in, f)
	short := asst("u4", "s1", "M4", 5)
	if err := os.WriteFile(f, []byte(short), 0o644); err != nil {
		t.Fatal(err)
	}
	mustFile(t, in, f)
	cur, _, _ := ledger.GetCursor(s.DB, f)
	if rows(t, s) != 4 || cur.BytesRead != int64(len(short)) {
		t.Fatalf("rows %d bytes_read %d", rows(t, s), cur.BytesRead)
	}
}

// 2026-09-29: crash recovery emptied the Python ledger while its process cache
// still marked every unchanged file as read. Here the cursor row is the only
// skip state, so an emptied ledger is read again in full.
func TestAWipedLedgerIsReadAgainInFull(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "a.jsonl"), asst("u1", "a", "M1", 5))
	write(t, filepath.Join(root, "-p", "b.jsonl"), asst("u2", "b", "M2", 5))
	mustSweep(t, in, root)
	if _, err := s.DB.Exec(`DELETE FROM responses; DELETE FROM files`); err != nil {
		t.Fatal(err)
	}
	st := mustSweep(t, in, root)
	if st.NewRows != 2 || rows(t, s) != 2 {
		t.Fatalf("new %d rows %d", st.NewRows, rows(t, s))
	}
}

func TestADeletedTranscriptKeepsItsRows(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	write(t, f, asst("u1", "s1", "M1", 5))
	mustSweep(t, in, root)
	if err := os.Remove(f); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d", n)
	}
}

func TestALineLargerThanTheReadBufferIsParsed(t *testing.T) {
	s, in, root := setup(t)
	f := filepath.Join(root, "-p", "s1.jsonl")
	big := strings.Repeat("x", 3<<20)
	write(t, f, fmt.Sprintf(`{"type":"user","uuid":"b1","sessionId":"s1","message":{"role":"user","content":%q}}`+"\n", big),
		asst("u1", "s1", "M1", 5))
	mustFile(t, in, f)
	if n := rows(t, s); n != 1 {
		t.Fatalf("rows %d", n)
	}
}

func TestTwoReadersAtOnceStoreEachResponseOnce(t *testing.T) {
	dir := t.TempDir()
	root := filepath.Join(dir, "projects")
	var lines []string
	for i := range 200 {
		lines = append(lines, asst(fmt.Sprintf("u%d", i), "s1", fmt.Sprintf("M%d", i), 5))
	}
	write(t, filepath.Join(root, "-p", "s1.jsonl"), lines...)
	stores := make([]*ledger.Store, 2)
	for i := range stores {
		st, err := ledger.Open(filepath.Join(dir, "ledger.db"))
		if err != nil {
			t.Fatal(err)
		}
		defer st.Close()
		stores[i] = st
	}
	var wg sync.WaitGroup
	errs := make([]error, 2)
	for i := range stores {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, errs[i] = (&Ingester{Store: stores[i]}).Sweep(root)
		}()
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if n := rows(t, stores[0]); n != 200 {
		t.Fatalf("rows %d", n)
	}
}

// A resumed session copies its parent's responses under new uuids; the parent
// must be read first so it keeps them. The names sort the other way round.
func TestSweepReadsTheOldestFileFirst(t *testing.T) {
	s, in, root := setup(t)
	parent := filepath.Join(root, "-p", "zz-parent.jsonl")
	child := filepath.Join(root, "-p", "aa-child.jsonl")
	write(t, parent, asst("u1", "parent", "M1", 5))
	write(t, child, asst("u9", "child", "M1", 5))
	old := time.Now().Add(-time.Hour)
	if err := os.Chtimes(parent, old, old); err != nil {
		t.Fatal(err)
	}
	mustSweep(t, in, root)
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses`).Scan(&sid)
	if sid != "parent" {
		t.Fatalf("billed to %s", sid)
	}
}

func TestSweepReadsSubagentsAndSkipsDotDirectories(t *testing.T) {
	s, in, root := setup(t)
	write(t, filepath.Join(root, "-p", "s1", "subagents", "agent-a.jsonl"), asst("u1", "s1", "M1", 5))
	write(t, filepath.Join(root, ".trash", "x.jsonl"), asst("u2", "s2", "M2", 5))
	st := mustSweep(t, in, root)
	if st.Files != 1 || rows(t, s) != 1 {
		t.Fatalf("files %d rows %d", st.Files, rows(t, s))
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/ingest"`
Expected: FAIL to build, `undefined: Ingester`.

- [ ] **Step 3: Write the implementation**

`core/internal/ingest/parse.go`:

```go
// Package ingest reads Claude Code transcripts (<projects>/**/*.jsonl) into the
// ledger, incrementally, by byte offset.
package ingest

import (
	"database/sql"
	"encoding/json"
	"strings"

	"fdcore/internal/ledger"
)

// record holds the fields any line type may carry. `message` stays raw: its
// shape differs by line type and only assistant lines need it decoded.
type record struct {
	Type        string          `json:"type"`
	UUID        string          `json:"uuid"`
	SessionID   string          `json:"sessionId"`
	CWD         string          `json:"cwd"`
	Timestamp   string          `json:"timestamp"`
	RequestID   string          `json:"requestId"`
	CustomTitle string          `json:"customTitle"`
	AITitle     string          `json:"aiTitle"`
	Message     json.RawMessage `json:"message"`
}

type assistantMessage struct {
	ID    string          `json:"id"`
	Model string          `json:"model"`
	Usage json.RawMessage `json:"usage"`
}

type usage struct {
	InputTokens   int64  `json:"input_tokens"`
	CacheRead     int64  `json:"cache_read_input_tokens"`
	OutputTokens  int64  `json:"output_tokens"`
	ServiceTier   string `json:"service_tier"`
	Speed         string `json:"speed"`
	InferenceGeo  string `json:"inference_geo"`
	CacheCreation struct {
		E5m int64 `json:"ephemeral_5m_input_tokens"`
		E1h int64 `json:"ephemeral_1h_input_tokens"`
	} `json:"cache_creation"`
}

// billed turns an assistant line with usage into a ledger row. One API response
// is written as one line per content block, each repeating its usage, so the
// key is (message.id, requestId), not the line uuid.
func billed(r record) (ledger.Response, bool) {
	if r.Type != "assistant" || r.UUID == "" || len(r.Message) == 0 {
		return ledger.Response{}, false
	}
	var m assistantMessage
	if json.Unmarshal(r.Message, &m) != nil || noUsage(m.Usage) {
		return ledger.Response{}, false
	}
	var u usage
	if json.Unmarshal(m.Usage, &u) != nil {
		return ledger.Response{}, false
	}
	id := m.ID
	if id == "" {
		id = "uuid:" + r.UUID
	}
	return ledger.Response{
		MessageID: id, RequestID: r.RequestID, UUID: r.UUID,
		SessionID: r.SessionID, Project: r.CWD, Model: m.Model, TS: r.Timestamp,
		Input: u.InputTokens, CacheRead: u.CacheRead,
		Create5m: u.CacheCreation.E5m, Create1h: u.CacheCreation.E1h, Output: u.OutputTokens,
		ServiceTier: u.ServiceTier, Speed: u.Speed, Geo: u.InferenceGeo,
	}, true
}

// noUsage mirrors the Python reader's `if not usage`: absent, null and {} are not billed.
func noUsage(raw json.RawMessage) bool {
	s := strings.TrimSpace(string(raw))
	return s == "" || s == "null" || s == "{}"
}

func title(tx *sql.Tx, r record) error {
	switch {
	case r.Type == "custom-title" && r.SessionID != "" && r.CustomTitle != "":
		return ledger.SetTitle(tx, r.SessionID, r.CustomTitle, "custom")
	case r.Type == "ai-title" && r.SessionID != "" && r.AITitle != "":
		return ledger.SetTitle(tx, r.SessionID, r.AITitle, "ai")
	}
	return nil
}
```

`core/internal/ingest/file.go`:

```go
package ingest

import (
	"bufio"
	"encoding/json"
	"io"
	"os"

	"fdcore/internal/ledger"
)

// Ingester reads transcripts into one ledger.
type Ingester struct{ Store *ledger.Store }

// File reads what is new in one transcript and returns the new billed rows. The
// cursor row is the only "already read" state, so a wiped ledger is read again
// in full; the Python reader's process cache left 520 files unread after the
// 2026-09-29 crash. Rows and cursor commit together.
func (in *Ingester) File(path string) (int, error) {
	st, err := os.Stat(path)
	if err != nil {
		return 0, nil // removed between listing and reading; its rows stay
	}
	cur, seen, err := ledger.GetCursor(in.Store.DB, path)
	if err != nil {
		return 0, err
	}
	mtime := st.ModTime().UnixNano()
	if seen && cur.MtimeNS == mtime && cur.Size == st.Size() {
		return 0, nil
	}
	var start, lines int64
	if seen && st.Size() >= cur.BytesRead {
		start, lines = cur.BytesRead, cur.Lines
	} // else new, or shorter than what was read (rewritten): from the top; keys dedupe
	f, err := os.Open(path)
	if err != nil {
		return 0, nil
	}
	defer f.Close()
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return 0, err
	}
	tx, err := in.Store.DB.Begin()
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	rd := bufio.NewReaderSize(f, 1<<20)
	added, consumed := 0, int64(0)
	for {
		raw, err := rd.ReadBytes('\n')
		if err == io.EOF {
			break // a trailing line without "\n" is still being written
		}
		if err != nil {
			return 0, err
		}
		consumed += int64(len(raw))
		lines++
		var r record
		if json.Unmarshal(raw, &r) != nil {
			continue // torn write: one real file has a run of NUL bytes
		}
		if resp, ok := billed(r); ok {
			o, err := ledger.Upsert(tx, resp)
			if err != nil {
				return 0, err
			}
			if o == ledger.New {
				added++
			}
			continue
		}
		if err := title(tx, r); err != nil {
			return 0, err
		}
	}
	if err := ledger.PutCursor(tx, ledger.Cursor{Path: path, MtimeNS: mtime, Size: st.Size(),
		BytesRead: start + consumed, Lines: lines}); err != nil {
		return 0, err
	}
	return added, tx.Commit()
}
```

`core/internal/ingest/sweep.go`:

```go
package ingest

import (
	"fmt"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
)

type Stats struct{ Files, NewRows int }

type Transcript struct {
	Path    string
	MtimeNS int64
}

// Sweep reads every transcript under root, oldest first. On a cold read the
// order decides attribution: a resumed session copies its parent's responses,
// and the first session to bring a response keeps it.
func (in *Ingester) Sweep(root string) (Stats, error) {
	var st Stats
	files, err := List(root)
	if err != nil {
		return st, err
	}
	for _, f := range files {
		n, err := in.File(f.Path)
		if err != nil {
			return st, fmt.Errorf("%s: %w", f.Path, err)
		}
		st.Files++
		st.NewRows += n
	}
	return st, nil
}

// List finds *.jsonl under root, skipping names that start with "." as
// Python's glob("**/*.jsonl") does, ordered by mtime and then path.
func List(root string) ([]Transcript, error) {
	var out []Transcript
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if p == root {
				return err
			}
			return nil // an unreadable subtree is skipped, the sweep goes on
		}
		if p != root && strings.HasPrefix(d.Name(), ".") {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".jsonl") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		out = append(out, Transcript{p, info.ModTime().UnixNano()})
		return nil
	})
	sort.Slice(out, func(i, j int) bool {
		if out[i].MtimeNS != out[j].MtimeNS {
			return out[i].MtimeNS < out[j].MtimeNS
		}
		return out[i].Path < out[j].Path
	})
	return out, err
}
```

- [ ] **Step 4: Run the tests**

Run: `make go-test GOTEST_ARGS="./internal/ingest -v"`
Expected: 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/internal/ingest
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): incremental transcript ingest with the cursor in the ledger"
```

---

### Task 5: Metrics

**Files:**
- Create: `core/internal/metrics/grain.go`
- Create: `core/internal/metrics/summary.go`
- Create: `core/internal/metrics/daily.go`
- Create: `core/internal/metrics/bymodel.go`
- Create: `core/internal/metrics/sessions.go`
- Test: `core/internal/metrics/metrics_test.go`

**Interfaces:**
- Consumes: `pricing.Table` (its `Cost`, `CacheSavings`, `RateFor`), `pricing.Usage`, `pricing.Row` (Task 2); `ledger.PriceTable`, and in tests `ledger.Open`, `ledger.Upsert`, `ledger.SetTitle`, `ledger.SetPrice`, `ledger.Response` (Task 3); tables `responses`, `session_meta`.
- Produces:
  - `type Cell struct { Day, Model, SessionID, Speed, Geo string; Turns, Input, CacheRead, Create5m, Create1h, Output int64; FirstTS, LastTS string }`
  - `func Grain(db *sql.DB, since string) ([]Cell, error)`
  - `func Since(rangeKey string, now time.Time) string`
  - `type Summary struct` (13 JSON keys of `/api/summary`); `func Summarize(prices *pricing.Table, cells []Cell, subscriptionMonthlyUSD float64) Summary`
  - `type Day struct` (10 keys of `/api/daily`); `func Daily(prices *pricing.Table, cells []Cell) []Day`
  - `type ModelRow struct` (7 keys of `/api/by-model`); `func ByModel(prices *pricing.Table, cells []Cell) []ModelRow`
  - `type SessionRow struct` (16 keys of `/api/sessions`, no `subagents`); `func Sessions(db *sql.DB, prices *pricing.Table, cells []Cell, limit, offset int) ([]SessionRow, error)`

- [ ] **Step 1: Write the failing test**

`core/internal/metrics/metrics_test.go`:

```go
package metrics

import (
	"encoding/json"
	"math"
	"path/filepath"
	"testing"
	"time"

	"fdcore/internal/ledger"
	"fdcore/internal/pricing"
)

// Costs by hand, rates per MTok:
//
//	m1 Opus 5.5  (1000*4 + 1000*20 + 1e6*0.05*4)/1e6      = 0.224
//	m2 Opus 5.5  (500*20 + 100000*2*4)/1e6                 = 0.81
//	m3 Fable 5.1 (2000*10 + 100*50 + 400000*0.025*10)/1e6  = 0.125
//	m4 <synthetic> and m5 claude-sonnet-3-7 are unpriced.
func fixture(t *testing.T) *ledger.Store {
	t.Helper()
	s, err := ledger.Open(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	rows := []ledger.Response{
		{MessageID: "m1", UUID: "u1", SessionID: "s1", Project: "/a", Model: "claude-opus-5-5",
			TS: "2026-09-24T10:00:00.000Z", Input: 1000, CacheRead: 1_000_000, Output: 1000},
		{MessageID: "m2", UUID: "u2", SessionID: "s1", Project: "/a", Model: "claude-opus-5-5",
			TS: "2026-09-25T10:00:00.000Z", Create1h: 100_000, Output: 500},
		{MessageID: "m3", UUID: "u3", SessionID: "s2", Project: "/b", Model: "claude-fable-5-1",
			TS: "2026-09-25T11:00:00.000Z", Input: 2000, CacheRead: 400_000, Output: 100},
		{MessageID: "m4", UUID: "u4", SessionID: "s2", Project: "/b", Model: "<synthetic>",
			TS: "2026-09-25T11:30:00.000Z", Input: 10},
		{MessageID: "m5", UUID: "u5", SessionID: "s3", Model: "claude-sonnet-3-7",
			TS: "2026-09-25T12:00:00.000Z", Input: 50, Output: 5},
	}
	tx, err := s.DB.Begin()
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		if _, err := ledger.Upsert(tx, r); err != nil {
			t.Fatal(err)
		}
	}
	if err := ledger.SetTitle(tx, "s1", "Mine", "custom"); err != nil {
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	return s
}

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9*math.Max(1, math.Abs(want)) {
		t.Errorf("%s = %.12f, want %.12f", name, got, want)
	}
}

func grain(t *testing.T, s *ledger.Store, since string) []Cell {
	t.Helper()
	c, err := Grain(s.DB, since)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// table is the ledger's price table: the shipped defaults unless a test edits it.
func table(t *testing.T, s *ledger.Store) *pricing.Table {
	t.Helper()
	tbl, err := ledger.PriceTable(s.DB)
	if err != nil {
		t.Fatal(err)
	}
	return tbl
}

func TestSummary(t *testing.T) {
	s := fixture(t)
	got := Summarize(table(t, s), grain(t, s, ""), 200)
	near(t, "total_cost", got.TotalCost, 1.159)
	// uncached minus cached: m1 4.024-0.224, m2 0.41-0.81 (writes cost more), m3 4.025-0.125
	near(t, "cache_savings", got.CacheSavings, 7.3)
	near(t, "cache_hit_rate", got.CacheHitRate, 1_400_000.0/1_503_060)
	// 2026-09-24T10:00 .. 2026-09-25T12:00 is 1 whole day, +1 = 2 days of subscription
	near(t, "saved_vs_subscription", got.SavedVsSubscription, 1.159-200*(2/30.4375))
	near(t, "avg_context_per_turn", got.AvgContextPerTurn, 1_503_060.0/5)
	if got.MessageCount != 5 || got.SessionCount != 3 || got.InputTokens != 3060 ||
		got.OutputTokens != 1605 || got.InputTokensRaw != 1_503_060 ||
		got.TotalContextTokens != 1_503_060 || got.UnknownModelTokens != 65 {
		t.Errorf("counts %+v", got)
	}
}

func TestSinceDropsEarlierDays(t *testing.T) {
	s := fixture(t)
	got := Summarize(table(t, s), grain(t, s, "2026-09-25"), 0)
	near(t, "total_cost", got.TotalCost, 0.935)
	if got.SessionCount != 3 || got.MessageCount != 4 {
		t.Errorf("%+v", got)
	}
}

func TestDailyCountsSyntheticAndEveryToken(t *testing.T) {
	s := fixture(t)
	d := Daily(table(t, s), grain(t, s, ""))
	if len(d) != 2 || d[0].Date != "2026-09-24" || d[1].Date != "2026-09-25" {
		t.Fatalf("%+v", d)
	}
	near(t, "day 1 cost", d[0].Cost, 0.224)
	near(t, "day 2 cost", d[1].Cost, 0.935)
	if d[0].Tokens != 1_002_000 || d[1].Tokens != 502_665 || d[1].CacheWrite != 100_000 {
		t.Errorf("%+v", d)
	}
}

func TestByModelSkipsSyntheticAndMarksUnpriced(t *testing.T) {
	s := fixture(t)
	m := ByModel(table(t, s), grain(t, s, ""))
	if len(m) != 3 || m[0].Model != "claude-opus-5-5" || m[1].Model != "claude-fable-5-1" ||
		m[2].Model != "claude-sonnet-3-7" {
		t.Fatalf("%+v", m)
	}
	near(t, "opus cost", m[0].Cost, 1.034)
	if !m[0].Priced || m[2].Priced || m[0].InputRaw != 1_101_000 {
		t.Errorf("%+v", m)
	}
}

func TestSessionsNewestFirstWithTitleProjectAndModels(t *testing.T) {
	s := fixture(t)
	rows, err := Sessions(s.DB, table(t, s), grain(t, s, ""), 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 || rows[0].SessionID != "s3" || rows[1].SessionID != "s2" || rows[2].SessionID != "s1" {
		t.Fatalf("%+v", rows)
	}
	s1 := rows[2]
	if s1.Title == nil || *s1.Title != "Mine" || s1.Project == nil || *s1.Project != "/a" ||
		s1.FirstTS != "2026-09-24T10:00:00.000Z" || s1.Turns != 2 {
		t.Errorf("s1 %+v", s1)
	}
	near(t, "s1 cost", s1.Cost, 1.034)
	if m := rows[1].Models; len(m) != 2 || m[0] != "<synthetic>" || m[1] != "claude-fable-5-1" {
		t.Errorf("s2 models %v", m)
	}
	if rows[0].Project != nil {
		t.Errorf("s3 project %q", *rows[0].Project)
	}
	page, _ := Sessions(s.DB, table(t, s), grain(t, s, ""), 1, 1)
	if len(page) != 1 || page[0].SessionID != "s2" {
		t.Errorf("page %+v", page)
	}
	far, _ := Sessions(s.DB, table(t, s), grain(t, s, ""), 10, 99)
	if far == nil || len(far) != 0 {
		t.Errorf("offset past the end: %v", far)
	}
}

func TestSinceUsesTheLocalDate(t *testing.T) {
	now := time.Date(2026, 10, 1, 12, 0, 0, 0, time.Local)
	for k, want := range map[string]string{"today": "2026-10-01", "7d": "2026-09-25", "30d": "2026-09-02", "all": "", "bogus": ""} {
		if got := Since(k, now); got != want {
			t.Errorf("%s: %q, want %q", k, got, want)
		}
	}
}

// Costs are derived at read time, so a price edit restates history.
func TestAPriceEditRestatesHistory(t *testing.T) {
	s := fixture(t)
	tx, _ := s.DB.Begin()
	if err := ledger.SetPrice(tx, pricing.Row{Prefix: "claude-sonnet-3-7", Input: 1000, Output: 0}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	got := Summarize(table(t, s), grain(t, s, ""), 0)
	// m5 now costs 50 tokens x $1000/MTok = $0.05 and is no longer unknown.
	near(t, "total_cost", got.TotalCost, 1.159+0.05)
	if got.UnknownModelTokens != 10 {
		t.Errorf("unknown tokens %d", got.UnknownModelTokens)
	}
}

func TestAnEmptyLedgerGivesEmptyArrays(t *testing.T) {
	tbl := table(t, fixture(t))
	for name, v := range map[string]any{"daily": Daily(tbl, nil), "by_model": ByModel(tbl, nil)} {
		b, _ := json.Marshal(v)
		if string(b) != "[]" {
			t.Errorf("%s marshals as %s", name, b)
		}
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/metrics"`
Expected: FAIL to build, `undefined: Grain`.

- [ ] **Step 3: Write the implementation**

`core/internal/metrics/grain.go`:

```go
// Package metrics answers the spend questions from the ledger with the same
// numbers as backend/flightdeck/metrics.py, endpoint for endpoint.
package metrics

import (
	"database/sql"
	"time"

	"fdcore/internal/pricing"
)

// Cell aggregates the ledger at (UTC day, model, session, speed, geo). A rate
// never changes inside a UTC day (the pricing tests guard it), so one rate
// prices a whole cell exactly. NULL model, session, speed and geo read as "".
type Cell struct {
	Day, Model, SessionID, Speed, Geo           string
	Turns, Input, CacheRead, Create5m, Create1h int64
	Output                                      int64
	FirstTS, LastTS                             string // "" when no row in the cell has a ts
}

const grainSQL = `SELECT COALESCE(substr(ts,1,10),''), COALESCE(model,''), COALESCE(session_id,''),
  COALESCE(speed,''), COALESCE(inference_geo,''), COUNT(*),
  SUM(input_tokens), SUM(cache_read), SUM(cache_create_5m), SUM(cache_create_1h), SUM(output_tokens),
  COALESCE(MIN(ts),''), COALESCE(MAX(ts),'')
FROM responses`

// Grain reads the cells from `since` on: a bare YYYY-MM-DD, or "" for everything.
func Grain(db *sql.DB, since string) ([]Cell, error) {
	q, args := grainSQL, []any{}
	if since != "" {
		q += " WHERE ts >= ?"
		args = append(args, since)
	}
	rows, err := db.Query(q+" GROUP BY 1, 2, 3, 4, 5", args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	cells := []Cell{}
	for rows.Next() {
		var c Cell
		if err := rows.Scan(&c.Day, &c.Model, &c.SessionID, &c.Speed, &c.Geo, &c.Turns,
			&c.Input, &c.CacheRead, &c.Create5m, &c.Create1h, &c.Output, &c.FirstTS, &c.LastTS); err != nil {
			return nil, err
		}
		cells = append(cells, c)
	}
	return cells, rows.Err()
}

func (c Cell) usage() pricing.Usage {
	return pricing.Usage{Model: c.Model, Input: c.Input, CacheRead: c.CacheRead,
		Create5m: c.Create5m, Create1h: c.Create1h, Out: c.Output,
		TS: c.FirstTS, Speed: c.Speed, Geo: c.Geo}
}

// context is every input-side token: uncached input, cache reads and cache writes.
func (c Cell) context() int64 { return c.Input + c.CacheRead + c.Create5m + c.Create1h }

// Since turns a UI range into a cutoff date. Like runtime.since() it takes the
// LOCAL calendar date and compares it with UTC timestamps; kept for parity.
func Since(rangeKey string, now time.Time) string {
	d := now.Local()
	switch rangeKey {
	case "today":
		return d.Format(time.DateOnly)
	case "7d":
		return d.AddDate(0, 0, -6).Format(time.DateOnly)
	case "30d":
		return d.AddDate(0, 0, -29).Format(time.DateOnly)
	}
	return ""
}
```

`core/internal/metrics/summary.go`:

```go
package metrics

import (
	"fmt"
	"time"

	"fdcore/internal/pricing"
)

const daysPerMonth = 30.4375

type Summary struct {
	TotalCost           float64 `json:"total_cost"`
	CacheSavings        float64 `json:"cache_savings"`
	SavedVsSubscription float64 `json:"saved_vs_subscription"`
	RTKSavings          float64 `json:"rtk_savings"` // not measured by fd yet
	CacheHitRate        float64 `json:"cache_hit_rate"`
	InputTokens         int64   `json:"input_tokens"`
	InputTokensRaw      int64   `json:"input_tokens_raw"`
	OutputTokens        int64   `json:"output_tokens"`
	SessionCount        int     `json:"session_count"`
	UnknownModelTokens  int64   `json:"unknown_model_tokens"`
	MessageCount        int64   `json:"message_count"`
	TotalContextTokens  int64   `json:"total_context_tokens"`
	AvgContextPerTurn   float64 `json:"avg_context_per_turn"`
}

func Summarize(prices *pricing.Table, cells []Cell, subscriptionMonthlyUSD float64) Summary {
	var s Summary
	var read, create int64
	sessions := map[string]bool{}
	minTS, maxTS := "", ""
	for _, c := range cells {
		s.MessageCount += c.Turns
		sessions[c.SessionID] = true
		read += c.CacheRead
		create += c.Create5m + c.Create1h
		s.InputTokens += c.Input
		s.OutputTokens += c.Output
		if c.FirstTS != "" && (minTS == "" || c.FirstTS < minTS) {
			minTS = c.FirstTS
		}
		if c.LastTS != "" && c.LastTS > maxTS {
			maxTS = c.LastTS
		}
		if cost, ok := prices.Cost(c.usage()); ok {
			s.TotalCost += cost
			s.CacheSavings += prices.CacheSavings(c.usage())
		} else {
			s.UnknownModelTokens += c.context() + c.Output
		}
	}
	s.SessionCount = len(sessions)
	denom := read + create + s.InputTokens
	s.InputTokensRaw, s.TotalContextTokens = denom, denom
	if denom > 0 {
		s.CacheHitRate = float64(read) / float64(denom)
	}
	if s.MessageCount > 0 {
		s.AvgContextPerTurn = float64(denom) / float64(s.MessageCount)
	}
	subscription := 0.0
	if a, err := parseTS(minTS); err == nil {
		if b, err := parseTS(maxTS); err == nil {
			days := int64(b.Sub(a)/(24*time.Hour)) + 1
			subscription = subscriptionMonthlyUSD * (float64(days) / daysPerMonth)
		}
	}
	s.SavedVsSubscription = s.TotalCost - subscription
	return s
}

// parseTS accepts what Python's datetime.fromisoformat accepted from the ledger.
func parseTS(v string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", time.DateOnly} {
		if t, err := time.Parse(layout, v); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unparseable timestamp %q", v)
}
```

`core/internal/metrics/daily.go`:

```go
package metrics

import (
	"sort"

	"fdcore/internal/pricing"
)

type Day struct {
	Date          string  `json:"date"`
	Cost          float64 `json:"cost"`
	Input         int64   `json:"input"`
	Output        int64   `json:"output"`
	CacheRead     int64   `json:"cache_read"`
	CacheWrite    int64   `json:"cache_write"`
	Tokens        int64   `json:"tokens"`
	AgentHours    float64 `json:"agent_hours"`     // 0 until the activity slice
	AgentHoursSub float64 `json:"agent_hours_sub"` // 0 until the activity slice
	AgentHoursEst float64 `json:"agent_hours_est"` // 0 until the activity slice
}

// Daily is one point per UTC day. `<synthetic>` turns count here (they are real
// turns on the chart) although ByModel leaves them out.
func Daily(prices *pricing.Table, cells []Cell) []Day {
	byDay := map[string]*Day{}
	for _, c := range cells {
		d := byDay[c.Day]
		if d == nil {
			d = &Day{Date: c.Day}
			byDay[c.Day] = d
		}
		d.Input += c.Input
		d.Output += c.Output
		d.CacheRead += c.CacheRead
		d.CacheWrite += c.Create5m + c.Create1h
		d.Tokens += c.context() + c.Output
		if cost, ok := prices.Cost(c.usage()); ok {
			d.Cost += cost
		}
	}
	out := make([]Day, 0, len(byDay))
	for _, d := range byDay {
		out = append(out, *d)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Date < out[j].Date })
	return out
}
```

`core/internal/metrics/bymodel.go`:

```go
package metrics

import (
	"sort"

	"fdcore/internal/pricing"
)

// Claude Code writes "<synthetic>" turns (local command output); not a billable model.
const syntheticModel = "<synthetic>"

type ModelRow struct {
	Model     string  `json:"model"`
	Input     int64   `json:"input"`
	InputRaw  int64   `json:"input_raw"`
	Output    int64   `json:"output"`
	CacheRead int64   `json:"cache_read"`
	Cost      float64 `json:"cost"`
	Priced    bool    `json:"priced"`
}

// ByModel sums per-day cells, so a model that spans a rate change is priced on
// each side of it rather than at one blended rate.
func ByModel(prices *pricing.Table, cells []Cell) []ModelRow {
	agg := map[string]*ModelRow{}
	for _, c := range cells {
		if c.Model == syntheticModel {
			continue
		}
		m := agg[c.Model]
		if m == nil {
			_, priced := prices.RateFor(c.Model, "")
			m = &ModelRow{Model: c.Model, Priced: priced}
			agg[c.Model] = m
		}
		m.Input += c.Input
		m.InputRaw += c.context()
		m.Output += c.Output
		m.CacheRead += c.CacheRead
		if cost, ok := prices.Cost(c.usage()); ok {
			m.Cost += cost
		}
	}
	out := make([]ModelRow, 0, len(agg))
	for _, m := range agg {
		out = append(out, *m)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Cost != out[j].Cost {
			return out[i].Cost > out[j].Cost
		}
		return out[i].Model < out[j].Model
	})
	return out
}
```

`core/internal/metrics/sessions.go`:

```go
package metrics

import (
	"database/sql"
	"sort"

	"fdcore/internal/pricing"
)

type SessionRow struct {
	SessionID   string   `json:"session_id"`
	Project     *string  `json:"project"`
	Models      []string `json:"models"`
	FirstTS     string   `json:"first_ts"`
	LastTS      string   `json:"last_ts"`
	Turns       int64    `json:"turns"`
	Input       int64    `json:"input"`
	Output      int64    `json:"output"`
	CacheRead   int64    `json:"cache_read"`
	CacheCreate int64    `json:"cache_create"`
	Cost        float64  `json:"cost"`
	Title       *string  `json:"title"`
	Context     int64    `json:"context"`
	InputRaw    int64    `json:"input_raw"`
	AvgContext  float64  `json:"avg_context"`
	CacheRatio  float64  `json:"cache_ratio"`
}

// Sessions pages sessions by last activity, newest first. The Python endpoint
// also attaches `subagents`, read from transcript files per request; that waits
// for a later slice.
func Sessions(db *sql.DB, prices *pricing.Table, cells []Cell, limit, offset int) ([]SessionRow, error) {
	type acc struct {
		row    SessionRow
		models map[string]bool
	}
	agg := map[string]*acc{}
	for _, c := range cells {
		a := agg[c.SessionID]
		if a == nil {
			a = &acc{row: SessionRow{SessionID: c.SessionID, FirstTS: c.FirstTS, LastTS: c.LastTS},
				models: map[string]bool{}}
			agg[c.SessionID] = a
		}
		r := &a.row
		a.models[c.Model] = true
		r.Turns += c.Turns
		if c.FirstTS < r.FirstTS {
			r.FirstTS = c.FirstTS
		}
		if c.LastTS > r.LastTS {
			r.LastTS = c.LastTS
		}
		r.Input += c.Input
		r.Output += c.Output
		r.CacheRead += c.CacheRead
		r.CacheCreate += c.Create5m + c.Create1h
		if cost, ok := prices.Cost(c.usage()); ok {
			r.Cost += cost
		}
	}
	projects, err := bySession(db, `SELECT COALESCE(session_id,''), MAX(project) FROM responses GROUP BY 1`)
	if err != nil {
		return nil, err
	}
	titles, err := bySession(db, `SELECT session_id, title FROM session_meta`)
	if err != nil {
		return nil, err
	}
	out := make([]SessionRow, 0, len(agg))
	for id, a := range agg {
		r := a.row
		r.Project, r.Title = projects[id], titles[id]
		r.Context = r.Input + r.CacheRead + r.CacheCreate
		r.InputRaw = r.Context
		if r.Turns > 0 {
			r.AvgContext = float64(r.Context) / float64(r.Turns)
		}
		if r.Context > 0 {
			r.CacheRatio = float64(r.CacheRead) / float64(r.Context)
		}
		r.Models = []string{}
		for m := range a.models {
			if m != "" {
				r.Models = append(r.Models, m)
			}
		}
		sort.Strings(r.Models)
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].LastTS != out[j].LastTS {
			return out[i].LastTS > out[j].LastTS
		}
		return out[i].SessionID < out[j].SessionID
	})
	offset = min(max(offset, 0), len(out))
	end := min(offset+max(limit, 0), len(out))
	return out[offset:end], nil
}

// bySession reads (session, value) pairs; a NULL value stays nil (JSON null).
func bySession(db *sql.DB, q string) (map[string]*string, error) {
	rows, err := db.Query(q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	m := map[string]*string{}
	for rows.Next() {
		var id string
		var v sql.NullString
		if err := rows.Scan(&id, &v); err != nil {
			return nil, err
		}
		if v.Valid {
			s := v.String
			m[id] = &s
		}
	}
	return m, rows.Err()
}
```

- [ ] **Step 4: Run the tests**

Run: `make go-test GOTEST_ARGS="./internal/metrics -v"`
Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/internal/metrics
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): spend metrics on the (day, model, session) grain"
```

---

### Task 6: CLI commands `ingest`, `spend`, `sessions`, `pricing`

**Files:**
- Create: `core/cmd/fd/flags.go`
- Create: `core/cmd/fd/spend.go`
- Create: `core/cmd/fd/pricing.go`
- Test: `core/cmd/fd/spend_test.go`
- Test: `core/cmd/fd/pricing_test.go`

**Interfaces:**
- Consumes: Task 1 helpers; `ledger.Open`, `ledger.PriceTable`, `ledger.Prices`, `ledger.SetPrice`, `ledger.ReplacePrices` (Task 3); `pricing.Config`, `pricing.Row`, `pricing.Defaults`, `pricing.Default*Mult` (Task 2); `ingest.Ingester`, `ingest.Stats` (Task 4); `metrics.Grain`, `metrics.Since`, `metrics.Summarize`, `metrics.ByModel`, `metrics.Daily`, `metrics.Sessions`, `metrics.Cell` (Task 5).
- Produces (package `main`, used by Tasks 7 and 9):
  - `type paths struct { db, projects string }`; `func addPaths(fs *flag.FlagSet) *paths`
  - `func envOr(k, def string) string`; `func envFloat(k string, def float64) float64`
  - `func ingestAll(s *ledger.Store, projects string) (ingest.Stats, error)` (refuses a missing directory)
  - `fd spend --json` prints `{"range", "summary", "by_model", "daily"}`; `fd sessions --json` prints a list; `fd ingest --json` prints `{"files", "new_rows", "seconds", "db"}`. Task 8 reads these shapes.
  - `fd pricing [list]` prints the table (`--json`: the `pricing.Config` shape); `fd pricing set <prefix> --input N --output N [--valid-from D] [--cache-read-mult M] [--write-5m-mult M] [--write-1h-mult M] [--fast-input N --fast-output N] [--note S]`; `fd pricing import <file|->` replaces the table; `fd pricing defaults` prints the shipped table.

- [ ] **Step 1: Write the failing test**

`core/cmd/fd/spend_test.go`:

```go
package main

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

// writeProjects holds one response written twice (two content blocks): one billed row.
func writeProjects(t *testing.T) (db, projects string) {
	t.Helper()
	dir := t.TempDir()
	projects = filepath.Join(dir, "projects")
	p := filepath.Join(projects, "-home-p", "s1.jsonl")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	line := `{"type":"assistant","uuid":"u1","sessionId":"s1","cwd":"/p","timestamp":"2026-09-25T07:00:00.000Z",` +
		`"requestId":"r1","message":{"id":"m1","model":"claude-opus-5-5","usage":{"input_tokens":1000,` +
		`"cache_read_input_tokens":1000000,"output_tokens":1000}}}` + "\n"
	if err := os.WriteFile(p, []byte(line+line), 0o644); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dir, "fd", "ledger.db"), projects
}

func TestSpendIngestsThenAnswers(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, errOut := runFd("spend", "--json", "--db", db, "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v struct {
		Range   string `json:"range"`
		Summary struct {
			TotalCost    float64 `json:"total_cost"`
			MessageCount int     `json:"message_count"`
		} `json:"summary"`
		ByModel []map[string]any `json:"by_model"`
		Daily   []map[string]any `json:"daily"`
	}
	oneDocument(t, out, &v)
	if v.Range != "all" || v.Summary.MessageCount != 1 || math.Abs(v.Summary.TotalCost-0.224) > 1e-9 ||
		len(v.ByModel) != 1 || len(v.Daily) != 1 {
		t.Fatalf("%+v", v)
	}
}

func TestSpendRefusesAnUnknownRange(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, _ := runFd("spend", "--json", "--range", "week", "--db", db, "--projects", projects)
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || v["error"] == "" {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestSpendRefusesAMissingProjectsDirectory(t *testing.T) {
	code, out, _ := runFd("spend", "--json", "--db", filepath.Join(t.TempDir(), "l.db"), "--projects", "/no/such/dir")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || v["error"] == "" {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestIngestCountsNewRowsOnce(t *testing.T) {
	db, projects := writeProjects(t)
	for i, want := range []float64{1, 0} {
		code, out, errOut := runFd("ingest", "--json", "--db", db, "--projects", projects)
		if code != 0 {
			t.Fatalf("run %d exit %d: %s", i, code, errOut)
		}
		var v map[string]any
		oneDocument(t, out, &v)
		if v["new_rows"] != want {
			t.Fatalf("run %d: %v", i, v)
		}
	}
}

func TestSessionsListsWithoutIngestWhenAsked(t *testing.T) {
	db, projects := writeProjects(t)
	code, out, _ := runFd("sessions", "--json", "--no-ingest", "--db", db, "--projects", projects)
	var empty []map[string]any
	oneDocument(t, out, &empty)
	if code != 0 || empty == nil || len(empty) != 0 {
		t.Fatalf("exit %d %v", code, empty)
	}
	code, out, _ = runFd("sessions", "--json", "--limit", "1", "--db", db, "--projects", projects)
	var rows []map[string]any
	oneDocument(t, out, &rows)
	if code != 0 || len(rows) != 1 || rows[0]["session_id"] != "s1" {
		t.Fatalf("exit %d %v", code, rows)
	}
}
```

`core/cmd/fd/pricing_test.go`:

```go
package main

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"fdcore/internal/pricing"
)

func listPrices(t *testing.T, db string) pricing.Config {
	t.Helper()
	code, out, errOut := runFd("pricing", "list", "--json", "--db", db)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var c pricing.Config
	oneDocument(t, out, &c)
	return c
}

func TestPricingListShowsTheShippedTable(t *testing.T) {
	c := listPrices(t, filepath.Join(t.TempDir(), "l.db"))
	if len(c.Models) != len(pricing.Defaults().Models) || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
}

func TestPricingSetChangesTheCostOfTokensAlreadyRead(t *testing.T) {
	db, projects := writeProjects(t)
	if code, _, errOut := runFd("pricing", "set", "claude-opus-5-5", "--input", "8", "--output", "40",
		"--cache-read-mult", "0.05", "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	code, out, errOut := runFd("spend", "--json", "--db", db, "--projects", projects)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	var v struct {
		Summary struct {
			TotalCost float64 `json:"total_cost"`
		} `json:"summary"`
	}
	oneDocument(t, out, &v)
	// (1000*8 + 1000*40 + 1e6*0.05*8) / 1e6
	if math.Abs(v.Summary.TotalCost-0.448) > 1e-9 {
		t.Fatalf("total_cost %v", v.Summary.TotalCost)
	}
}

func TestPricingSetRefusesWhatWouldPriceWrongly(t *testing.T) {
	db := filepath.Join(t.TempDir(), "l.db")
	for name, args := range map[string][]string{
		"time in the date": {"claude-x", "--input", "1", "--output", "2", "--valid-from", "2026-10-01T12:00"},
		"no output price":  {"claude-x", "--input", "1"},
		"half a fast rate": {"claude-x", "--input", "1", "--output", "2", "--fast-input", "3"},
		"no prefix":        {"--input", "1", "--output", "2"},
	} {
		code, out, _ := runFd(append([]string{"pricing", "set"}, append(args, "--json", "--db", db)...)...)
		var v map[string]string
		oneDocument(t, out, &v)
		if code != 2 || v["error"] == "" {
			t.Errorf("%s: exit %d %v", name, code, v)
		}
	}
	if n := len(listPrices(t, db).Models); n != len(pricing.Defaults().Models) {
		t.Errorf("%d rows after refusals", n)
	}
}

func TestPricingImportReplacesTheTableAndRefusesUnknownFields(t *testing.T) {
	dir := t.TempDir()
	db := filepath.Join(dir, "l.db")
	good := filepath.Join(dir, "good.json")
	os.WriteFile(good, []byte(`{"models":[{"prefix":"claude-opus-5-5","input":4,"output":20,"cache_read_mult":0.05}],"geo":{}}`), 0o644)
	if code, _, errOut := runFd("pricing", "import", good, "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	if c := listPrices(t, db); len(c.Models) != 1 || len(c.Geo) != 0 {
		t.Fatalf("%+v", c)
	}
	bad := filepath.Join(dir, "bad.json")
	os.WriteFile(bad, []byte(`{"models":[{"prefix":"claude-x","inputs":4}]}`), 0o644)
	code, out, _ := runFd("pricing", "import", bad, "--json", "--db", db)
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || !strings.Contains(v["error"], "inputs") {
		t.Fatalf("exit %d %v", code, v)
	}
}

func TestPricingDefaultsImportBackUnchanged(t *testing.T) {
	dir := t.TempDir()
	db := filepath.Join(dir, "l.db")
	_, out, _ := runFd("pricing", "defaults", "--json")
	file := filepath.Join(dir, "defaults.json")
	os.WriteFile(file, []byte(out), 0o644)
	if code, _, errOut := runFd("pricing", "import", file, "--json", "--db", db); code != 0 {
		t.Fatalf("exit %d: %s", code, errOut)
	}
	if c := listPrices(t, db); len(c.Models) != len(pricing.Defaults().Models) || c.Geo["us"] != 1.1 {
		t.Fatalf("%d models, geo %v", len(c.Models), c.Geo)
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./cmd/fd"`
Expected: FAIL; `spend`, `ingest` and `pricing` answer exit 3 (unknown command).

- [ ] **Step 3: Write the implementation**

`core/cmd/fd/flags.go`:

```go
package main

import (
	"flag"
	"os"
	"path/filepath"
	"strconv"

	"fdcore/internal/ingest"
	"fdcore/internal/ledger"
)

type paths struct{ db, projects string }

func addPaths(fs *flag.FlagSet) *paths {
	p := &paths{}
	fs.StringVar(&p.db, "db", envOr("FD_DB", filepath.Join(homeDir(), ".flightdeck", "fd", "ledger.db")),
		"ledger database file")
	fs.StringVar(&p.projects, "projects", envOr("FD_PROJECTS", defaultProjects()),
		"Claude Code projects directory")
	return p
}

func defaultProjects() string {
	if d := os.Getenv("CLAUDE_CONFIG_DIR"); d != "" {
		return filepath.Join(d, "projects")
	}
	return filepath.Join(homeDir(), ".claude", "projects")
}

func homeDir() string {
	h, _ := os.UserHomeDir()
	return h
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

// ingestAll brings the ledger up to date before an answer.
func ingestAll(s *ledger.Store, projects string) (ingest.Stats, error) {
	if st, err := os.Stat(projects); err != nil || !st.IsDir() {
		return ingest.Stats{}, refuse("projects directory %s not found", projects)
	}
	return (&ingest.Ingester{Store: s}).Sweep(projects)
}

func validRange(r string) error {
	switch r {
	case "today", "7d", "30d", "all":
		return nil
	}
	return refuse("--range must be today, 7d, 30d or all, not %q", r)
}
```

`core/cmd/fd/spend.go`:

```go
package main

import (
	"fmt"
	"io"
	"text/tabwriter"
	"time"

	"fdcore/internal/ledger"
	"fdcore/internal/metrics"
	"fdcore/internal/pricing"
)

func init() {
	commands["ingest"] = command{"read new transcript lines into the ledger", cmdIngest}
	commands["spend"] = command{"cost and tokens for a range: summary, by model, by day", cmdSpend}
	commands["sessions"] = command{"sessions by last activity, with cost", cmdSessions}
}

func cmdIngest(args []string) (output, error) {
	fs := newFlags("ingest")
	p := addPaths(fs)
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	t0 := time.Now()
	st, err := ingestAll(s, p.projects)
	if err != nil {
		return output{}, err
	}
	secs := time.Since(t0).Seconds()
	data := map[string]any{"files": st.Files, "new_rows": st.NewRows, "seconds": secs, "db": p.db}
	return output{Data: data, Text: func(w io.Writer) {
		fmt.Fprintf(w, "%d new responses from %d files in %.1fs into %s\n", st.NewRows, st.Files, secs, p.db)
	}}, nil
}

// ledgerCells opens the ledger, brings it up to date unless told not to, and
// reads the price table and the grain for a range. The caller closes the store.
func ledgerCells(p *paths, rng string, noIngest bool) (*ledger.Store, *pricing.Table, []metrics.Cell, error) {
	s, err := ledger.Open(p.db)
	if err != nil {
		return nil, nil, nil, err
	}
	fail := func(err error) (*ledger.Store, *pricing.Table, []metrics.Cell, error) {
		s.Close()
		return nil, nil, nil, err
	}
	if !noIngest {
		if _, err := ingestAll(s, p.projects); err != nil {
			return fail(err)
		}
	}
	prices, err := ledger.PriceTable(s.DB)
	if err != nil {
		return fail(err)
	}
	cells, err := metrics.Grain(s.DB, metrics.Since(rng, time.Now()))
	if err != nil {
		return fail(err)
	}
	return s, prices, cells, nil
}

func cmdSpend(args []string) (output, error) {
	fs := newFlags("spend")
	p := addPaths(fs)
	rng := fs.String("range", "all", "today, 7d, 30d or all")
	noIngest := fs.Bool("no-ingest", false, "answer from the ledger as it is")
	sub := fs.Float64("subscription-usd", envFloat("FD_SUBSCRIPTION_USD", 0),
		"monthly subscription price, for saved_vs_subscription")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if err := validRange(*rng); err != nil {
		return output{}, err
	}
	s, prices, cells, err := ledgerCells(p, *rng, *noIngest)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	sum := metrics.Summarize(prices, cells, *sub)
	models := metrics.ByModel(prices, cells)
	data := map[string]any{"range": *rng, "summary": sum, "by_model": models,
		"daily": metrics.Daily(prices, cells)}
	return output{Data: data, Text: func(w io.Writer) {
		fmt.Fprintf(w, "%s: $%.2f over %d responses in %d sessions, cache hit %.1f%%\n",
			*rng, sum.TotalCost, sum.MessageCount, sum.SessionCount, 100*sum.CacheHitRate)
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		fmt.Fprintln(tw, "model\tcost\toutput\tinput+cache")
		for _, m := range models {
			fmt.Fprintf(tw, "%s\t$%.2f\t%d\t%d\n", m.Model, m.Cost, m.Output, m.InputRaw)
		}
		tw.Flush()
	}}, nil
}

func cmdSessions(args []string) (output, error) {
	fs := newFlags("sessions")
	p := addPaths(fs)
	rng := fs.String("range", "all", "today, 7d, 30d or all")
	limit := fs.Int("limit", 20, "sessions per page")
	offset := fs.Int("offset", 0, "sessions to skip")
	noIngest := fs.Bool("no-ingest", false, "answer from the ledger as it is")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if err := validRange(*rng); err != nil {
		return output{}, err
	}
	if *limit < 0 || *offset < 0 {
		return output{}, refuse("--limit and --offset cannot be negative")
	}
	s, prices, cells, err := ledgerCells(p, *rng, *noIngest)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	rows, err := metrics.Sessions(s.DB, prices, cells, *limit, *offset)
	if err != nil {
		return output{}, err
	}
	return output{Data: rows, Text: func(w io.Writer) {
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		fmt.Fprintln(tw, "last activity\tcost\tturns\tsession")
		for _, r := range rows {
			name := r.SessionID
			if r.Title != nil {
				name = *r.Title
			} else if r.Project != nil {
				name = *r.Project
			}
			fmt.Fprintf(tw, "%s\t$%.2f\t%d\t%s\n", r.LastTS, r.Cost, r.Turns, name)
		}
		tw.Flush()
	}}, nil
}
```

`core/cmd/fd/pricing.go`:

```go
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
```

- [ ] **Step 4: Run the tests and try it on the real corpus**

```bash
SCRATCH=/tmp/claude-1000/-home-nathando-Documents-Projects/49f36a17-b0e7-432e-a0e6-cb405cddeea2/scratchpad
make go-test && make go-build
time core/bin/fd spend --range 7d --db "$SCRATCH/fd-try.db"
core/bin/fd ingest --db "$SCRATCH/fd-try.db"
core/bin/fd pricing --db "$SCRATCH/fd-try.db"
```

Expected: every package PASSES; a one-line summary and a model table (the `time` line is the cold ingest of the real corpus); the second command reports 0 new responses; the third prints the 17-row price table. Add `$SCRATCH/fd-try.db` to `TEMP_FILES_SHOULD_BE_REMOVE.MD`.

- [ ] **Step 5: Commit**

```bash
git add core/cmd/fd/flags.go core/cmd/fd/spend.go core/cmd/fd/spend_test.go core/cmd/fd/pricing.go core/cmd/fd/pricing_test.go
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): fd ingest, spend, sessions and pricing"
```

---

### Task 7: `fd serve`: the spend API and the watcher

**Files:**
- Create: `core/internal/server/server.go`
- Create: `core/internal/server/watch.go`
- Create: `core/cmd/fd/serve.go`
- Test: `core/internal/server/server_test.go`
- Modify: `core/go.mod`, `core/go.sum` (via `make go-tidy`)

**Interfaces:**
- Consumes: `ledger.Store`, `ledger.PriceTable` (Task 3); `pricing.Table` (Task 2); `ingest.Ingester` (Task 4); `metrics.*` (Task 5); `addPaths`, `envOr`, `envFloat`, `refuse`, `newFlags`, `parse`, `version` (Tasks 1, 6).
- Produces:
  - `type Server struct { Store *ledger.Store; Projects string; Subscription float64; UIDir, Version string; Now func() time.Time; Ready chan struct{} }`
  - `func (s *Server) Handler() http.Handler`
  - `func (s *Server) Watch(ctx context.Context, debounce, sweepEvery time.Duration) error`
  - `func (s *Server) SweepNow()`; `func (s *Server) IngestPaths(paths []string)`
  - Routes: `GET /api/summary|daily|by-model|sessions|health`; any other `/api/*` answers 404 `{"error": …}`; `/` serves `UIDir` with an `index.html` fallback when set.

- [ ] **Step 1: Write the failing test**

`core/internal/server/server_test.go`:

```go
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"fdcore/internal/ledger"
)

func line(uuid, msg string) string {
	return fmt.Sprintf(`{"type":"assistant","uuid":%q,"sessionId":"s1","cwd":"/p",`+
		`"timestamp":"2026-09-25T07:00:00.000Z","requestId":"r","message":{"id":%q,`+
		`"model":"claude-opus-5-5","usage":{"input_tokens":1,"output_tokens":2}}}`+"\n", uuid, msg)
}

func newServer(t *testing.T) (*Server, string) {
	t.Helper()
	dir := t.TempDir()
	projects := filepath.Join(dir, "projects")
	if err := os.MkdirAll(filepath.Join(projects, "-p"), 0o755); err != nil {
		t.Fatal(err)
	}
	st, err := ledger.Open(filepath.Join(dir, "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return &Server{Store: st, Projects: projects, Version: "test", Now: time.Now}, projects
}

func get(t *testing.T, s *Server, path string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, httptest.NewRequest("GET", path, nil))
	return rec
}

func appendTo(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(text); err != nil {
		t.Fatal(err)
	}
}

func waitRows(t *testing.T, s *Server, want int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		var n int
		s.Store.DB.QueryRow(`SELECT count(*) FROM responses`).Scan(&n)
		if n == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("rows %d after 5s, want %d", n, want)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func watch(t *testing.T, s *Server) {
	t.Helper()
	s.Ready = make(chan struct{})
	ctx, cancel := context.WithCancel(t.Context())
	t.Cleanup(cancel)
	go s.Watch(ctx, 50*time.Millisecond, time.Hour)
	<-s.Ready
}

func TestSummaryHasEveryFastAPIKey(t *testing.T) {
	s, projects := newServer(t)
	appendTo(t, filepath.Join(projects, "-p", "s1.jsonl"), line("u1", "m1"))
	s.SweepNow()
	rec := get(t, s, "/api/summary?range=all")
	var m map[string]any
	if rec.Code != 200 || json.Unmarshal(rec.Body.Bytes(), &m) != nil {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	for _, k := range []string{"total_cost", "cache_savings", "saved_vs_subscription", "rtk_savings",
		"cache_hit_rate", "input_tokens", "input_tokens_raw", "output_tokens", "session_count",
		"unknown_model_tokens", "message_count", "total_context_tokens", "avg_context_per_turn"} {
		if _, ok := m[k]; !ok {
			t.Errorf("missing %s", k)
		}
	}
	if m["message_count"] != 1.0 {
		t.Errorf("message_count %v", m["message_count"])
	}
}

func TestEmptyListsAreArrays(t *testing.T) {
	s, _ := newServer(t)
	for _, p := range []string{"/api/daily", "/api/by-model", "/api/sessions?limit=5"} {
		rec := get(t, s, p)
		if rec.Code != 200 || strings.TrimSpace(rec.Body.String()) != "[]" {
			t.Errorf("%s: %d %q", p, rec.Code, rec.Body)
		}
	}
}

func TestOtherAPIPathsAre404WithAnError(t *testing.T) {
	s, _ := newServer(t)
	rec := get(t, s, "/api/burn")
	var m map[string]string
	if rec.Code != 404 || json.Unmarshal(rec.Body.Bytes(), &m) != nil || m["error"] == "" {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
}

func TestAnAppendedLineIsReadWithinSeconds(t *testing.T) {
	s, projects := newServer(t)
	f := filepath.Join(projects, "-p", "s1.jsonl")
	appendTo(t, f, line("u1", "m1"))
	s.SweepNow()
	watch(t, s)
	appendTo(t, f, line("u2", "m2"))
	waitRows(t, s, 2)
}

func TestANewDirectoryIsWatched(t *testing.T) {
	s, projects := newServer(t)
	watch(t, s)
	appendTo(t, filepath.Join(projects, "-p", "s1", "subagents", "agent-a.jsonl"), line("u1", "m1"))
	waitRows(t, s, 1)
	appendTo(t, filepath.Join(projects, "-p", "s1", "subagents", "agent-a.jsonl"), line("u2", "m2"))
	waitRows(t, s, 2)
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/server"`
Expected: FAIL to build, `undefined: Server`.

- [ ] **Step 3: Write the implementation**

`core/internal/server/server.go`:

```go
// Package server is `fd serve`: the spend API the React UI reads, over the same
// ledger the CLI uses, kept current by a file watcher and a periodic sweep.
package server

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"fdcore/internal/ingest"
	"fdcore/internal/ledger"
	"fdcore/internal/metrics"
	"fdcore/internal/pricing"
)

type Server struct {
	Store        *ledger.Store
	Projects     string
	Subscription float64
	UIDir        string
	Version      string
	Now          func() time.Time
	Ready        chan struct{} // closed once the watches are in place; tests wait on it
	mu           sync.Mutex    // one ingest pass at a time, from the watcher or the sweep
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/summary", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.Summarize(p, c, s.Subscription))
	}))
	mux.HandleFunc("GET /api/daily", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.Daily(p, c))
	}))
	mux.HandleFunc("GET /api/by-model", s.cells(func(w http.ResponseWriter, _ *http.Request, p *pricing.Table, c []metrics.Cell) {
		writeJSON(w, 200, metrics.ByModel(p, c))
	}))
	mux.HandleFunc("GET /api/sessions", s.cells(func(w http.ResponseWriter, r *http.Request, p *pricing.Table, c []metrics.Cell) {
		rows, err := metrics.Sessions(s.Store.DB, p, c, intParam(r, "limit", 100), intParam(r, "offset", 0))
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, rows)
	}))
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "version": s.Version, "projects": s.Projects})
	})
	// The UI calls endpoints fd does not have yet; a JSON 404 fails them cleanly
	// instead of handing back index.html.
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 404, map[string]string{"error": "not served by fd yet: " + r.URL.Path})
	})
	if s.UIDir != "" {
		mux.Handle("/", spa(s.UIDir))
	}
	return mux
}

// cells reads the price table and the grain for the request's ?range= (missing
// or unknown = all, as FastAPI does). The table is read per request, so an
// `fd pricing` edit shows without a restart.
func (s *Server) cells(h func(http.ResponseWriter, *http.Request, *pricing.Table, []metrics.Cell)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		p, err := ledger.PriceTable(s.Store.DB)
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		c, err := metrics.Grain(s.Store.DB, metrics.Since(r.URL.Query().Get("range"), s.Now()))
		if err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
		h(w, r, p, c)
	}
}

func intParam(r *http.Request, k string, def int) int {
	if v, err := strconv.Atoi(r.URL.Query().Get(k)); err == nil {
		return v
	}
	return def
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// spa serves the built UI; unknown paths get index.html so client routes load.
func spa(dir string) http.Handler {
	files := http.FileServer(http.Dir(dir))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := filepath.Join(dir, filepath.Clean("/"+r.URL.Path))
		if st, err := os.Stat(p); err != nil || (st.IsDir() && r.URL.Path != "/") {
			http.ServeFile(w, r, filepath.Join(dir, "index.html"))
			return
		}
		files.ServeHTTP(w, r)
	})
}

func (s *Server) IngestPaths(paths []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	in := ingest.Ingester{Store: s.Store}
	for _, p := range paths {
		if _, err := in.File(p); err != nil {
			log.Printf("ingest %s: %v", p, err)
		}
	}
}

func (s *Server) SweepNow() {
	s.mu.Lock()
	defer s.mu.Unlock()
	st, err := (&ingest.Ingester{Store: s.Store}).Sweep(s.Projects)
	if err != nil {
		log.Printf("sweep: %v", err)
		return
	}
	if st.NewRows > 0 {
		log.Printf("sweep: %d new responses from %d files", st.NewRows, st.Files)
	}
}
```

`core/internal/server/watch.go`:

```go
package server

import (
	"context"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/fsnotify/fsnotify"
)

// Watch keeps the ledger current until ctx ends. inotify is not recursive, so
// every directory is watched and new ones are added as they appear; a new
// directory also triggers a sweep, because files can land in it before its
// watch exists. Only Write and Create count: the Python watcher once reacted to
// its own reads and re-ingested in a loop.
func (s *Server) Watch(ctx context.Context, debounce, sweepEvery time.Duration) error {
	w, err := fsnotify.NewWatcher()
	if err != nil {
		return err
	}
	defer w.Close()
	if err := watchTree(w, s.Projects); err != nil {
		return err
	}
	if s.Ready != nil {
		close(s.Ready)
	}
	pending := map[string]bool{}
	flush := time.NewTimer(debounce)
	flush.Stop()
	sweep := time.NewTicker(sweepEvery)
	defer sweep.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case ev, ok := <-w.Events:
			if !ok {
				return nil
			}
			if !ev.Has(fsnotify.Write) && !ev.Has(fsnotify.Create) {
				continue
			}
			if st, err := os.Stat(ev.Name); err == nil && st.IsDir() {
				if err := watchTree(w, ev.Name); err != nil {
					log.Printf("watch %s: %v", ev.Name, err)
				}
				s.SweepNow()
				continue
			}
			if strings.HasSuffix(ev.Name, ".jsonl") {
				pending[ev.Name] = true
				flush.Reset(debounce)
			}
		case err, ok := <-w.Errors:
			if !ok {
				return nil
			}
			log.Printf("watch: %v", err)
		case <-flush.C:
			paths := make([]string, 0, len(pending))
			for p := range pending {
				paths = append(paths, p)
			}
			pending = map[string]bool{}
			s.IngestPaths(paths)
		case <-sweep.C:
			s.SweepNow()
		}
	}
}

func watchTree(w *fsnotify.Watcher, root string) error {
	return filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if p == root {
				return err
			}
			return nil
		}
		if !d.IsDir() {
			return nil
		}
		if p != root && strings.HasPrefix(d.Name(), ".") {
			return filepath.SkipDir
		}
		return w.Add(p)
	})
}
```

`core/cmd/fd/serve.go`:

```go
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"fdcore/internal/ledger"
	"fdcore/internal/server"
)

func init() {
	commands["serve"] = command{"keep the ledger current and serve the spend API (and the UI)", cmdServe}
}

func cmdServe(args []string) (output, error) {
	fs := newFlags("serve")
	p := addPaths(fs)
	addr := fs.String("addr", envOr("FD_ADDR", "127.0.0.1:8011"), "listen address; the Python backend holds :8010")
	ui := fs.String("ui", "", "directory of the built React UI (frontend/dist); empty serves the API only")
	sub := fs.Float64("subscription-usd", envFloat("FD_SUBSCRIPTION_USD", 0),
		"monthly subscription price, for saved_vs_subscription")
	debounce := fs.Duration("debounce", 2*time.Second, "wait after a transcript write before reading it")
	sweepEvery := fs.Duration("sweep", 5*time.Minute, "re-check every transcript this often, in case an event was missed")
	if err := parse(fs, args); err != nil {
		return output{}, err
	}
	if st, err := os.Stat(p.projects); err != nil || !st.IsDir() {
		return output{}, refuse("projects directory %s not found", p.projects)
	}
	s, err := ledger.Open(p.db)
	if err != nil {
		return output{}, err
	}
	defer s.Close()
	srv := &server.Server{Store: s, Projects: p.projects, Subscription: *sub, UIDir: *ui,
		Version: version, Now: time.Now}
	log.SetOutput(os.Stderr)
	srv.SweepNow()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		err := srv.Watch(ctx, *debounce, *sweepEvery)
		if err == nil {
			return
		}
		log.Printf("watch unavailable (%v); sweeping every %s instead", err, *sweepEvery)
		t := time.NewTicker(*sweepEvery)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				srv.SweepNow()
			}
		}
	}()
	hs := &http.Server{Addr: *addr, Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		hs.Shutdown(context.Background())
	}()
	log.Printf("fd %s serving %s from %s", version, *addr, p.db)
	if err := hs.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return output{}, refuse("listen %s: %v", *addr, err)
	}
	return output{Data: map[string]string{"stopped": *addr}}, nil
}
```

- [ ] **Step 4: Resolve the dependency and run the tests**

Run: `make go-tidy && make go-test`
Expected: `go.mod` gains `github.com/fsnotify/fsnotify`; every package PASSES.

- [ ] **Step 5: Look at it in a browser**

Run: `make build` (if `frontend/dist` is stale), `make go-build`, then `core/bin/fd serve --ui frontend/dist --subscription-usd 200 --db "$SCRATCH/fd-try.db"` (the Task 6 file) and open `http://127.0.0.1:8011`.
Expected: the Spend view shows totals and the model table; panels backed by endpoints fd does not serve show their error state. Take a screenshot to `devtools_mcp_trace/` with headless Playwright (`NODE_PATH=$(npm root -g)`), and note which panels fail.

- [ ] **Step 6: Commit**

```bash
git add core/go.mod core/go.sum core/internal/server core/cmd/fd/serve.go
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): fd serve with the spend API and a recursive watcher"
```

---

### Task 8: Parity against the Python backend

**Files:**
- Create: `scripts/go-parity/python_metrics.py`
- Create: `scripts/go-parity/compare.py`
- Modify: `docs/2026-10-01-go-core-binary.md` (append a "Measured" section)

**Interfaces:**
- Consumes: the Python backend (`flightdeck.db`, `flightdeck.ingest`, `flightdeck.metrics`, unchanged); `fd ingest --json`, `fd spend --json`, `fd sessions --json` shapes from Task 6.
- Produces: an exit-0 parity run on a corpus snapshot, and the measured numbers in the spec.

- [ ] **Step 1: Write the oracle**

`scripts/go-parity/python_metrics.py`:

```python
"""Spend metrics from the Python backend over a fresh SQLite ledger, as JSON.

The oracle for the Go core: the same transcripts, read in the same order (oldest
file first, as `fd` does), into a throwaway SQLite file, then the four spend
answers for every range. Never touches the live Postgres ledger.

    PYTHONPATH=backend .venv/bin/python scripts/go-parity/python_metrics.py \
        --projects ~/.cache/fd-parity/projects --db "$SCRATCH/py.db" \
        --subscription-usd 200 > "$SCRATCH/py.json"
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import date, timedelta

from flightdeck import db, ingest, metrics

RANGES = ("today", "7d", "30d", "all")


def since(range_key):
    # Copied from runtime.since(): importing runtime would pull in the FastAPI app.
    today = date.today()
    if range_key == "today":
        return today.isoformat()
    if range_key == "7d":
        return (today - timedelta(days=6)).isoformat()
    if range_key == "30d":
        return (today - timedelta(days=29)).isoformat()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--subscription-usd", type=float, default=0.0)
    a = ap.parse_args()
    if os.path.exists(a.db):
        sys.exit(f"{a.db} exists; the oracle needs a fresh ledger")
    db.configure({})  # no database_url: SQLite at a.db, whatever the environment says
    conn = db.connect(a.db)
    root = os.path.expanduser(a.projects)
    paths = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    paths.sort(key=lambda p: (os.stat(p).st_mtime_ns, p))
    t0 = time.time()
    for p in paths:
        ingest.ingest_file(conn, p)
    print(f"python ingest: {len(paths)} files in {time.time() - t0:.1f}s", file=sys.stderr)
    out = {}
    for r in RANGES:
        s = since(r)
        out[r] = {
            "summary": metrics.summary(conn, a.subscription_usd, 0.0, since=s),
            "daily": metrics.daily(conn, since=s),
            "by_model": metrics.by_model(conn, since=s),
            "sessions": metrics.sessions(conn, limit=10**7, since=s),
        }
    json.dump(out, sys.stdout, default=str)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the comparison**

`scripts/go-parity/compare.py`:

```python
"""Diff the Go core against the Python oracle, field by field.

    python3 scripts/go-parity/compare.py --py "$SCRATCH/py.json" --fd core/bin/fd \
        --db "$SCRATCH/fd.db" --projects ~/.cache/fd-parity/projects --subscription-usd 200

Exit 0 when every compared value agrees (numbers within 1e-9 relative), 1 otherwise.
Not compared in slice 1 (see the spec): rtk_savings, agent_hours*, sessions[].subagents.
"""
import argparse
import json
import math
import subprocess
import sys
import time

RANGES = ("today", "7d", "30d", "all")
SKIP = {"rtk_savings", "agent_hours", "agent_hours_sub", "agent_hours_est", "subagents"}


def fd(binary, *args):
    done = subprocess.run([binary, *args, "--json"], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"fd {' '.join(args)} exited {done.returncode}: {done.stdout}{done.stderr}")
    return json.loads(done.stdout)


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if a in (None, "") and b in (None, ""):
        return True
    return a == b


def diff(path, a, b, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k in SKIP:
                continue
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'go' if k in b else 'python'}")
                continue
            diff(f"{path}.{k}", a[k], b[k], out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} in python, {len(b)} in go")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(f"{path}[{i}]", x, y, out)
    elif not same(a, b):
        out.append(f"{path}: python {a!r} go {b!r}")


def keyed(rows, key):
    return {(r[key] or ""): r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", required=True)
    ap.add_argument("--fd", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--projects", required=True)
    ap.add_argument("--subscription-usd", default="0")
    a = ap.parse_args()
    py = json.load(open(a.py))
    t0 = time.time()
    ing = fd(a.fd, "ingest", "--db", a.db, "--projects", a.projects)
    print(f"go ingest: {ing['files']} files, {ing['new_rows']} responses in {time.time() - t0:.1f}s")
    problems = []
    for r in RANGES:
        spend = fd(a.fd, "spend", "--no-ingest", "--range", r, "--db", a.db,
                   "--subscription-usd", a.subscription_usd)
        sessions = fd(a.fd, "sessions", "--no-ingest", "--range", r, "--limit", "10000000", "--db", a.db)
        p = py[r]
        # Python adds a day that has agent busy time but no billed response (a turn
        # running past midnight); fd has no activity yet, so such days are dropped.
        p_daily = [d for d in p["daily"] if d["tokens"] or d["cost"]]
        diff(f"{r}.summary", p["summary"], spend["summary"], problems)
        diff(f"{r}.daily", keyed(p_daily, "date"), keyed(spend["daily"], "date"), problems)
        diff(f"{r}.by_model", keyed(p["by_model"], "model"), keyed(spend["by_model"], "model"), problems)
        diff(f"{r}.sessions", keyed(p["sessions"], "session_id"), keyed(sessions, "session_id"), problems)
    for line in problems[:50]:
        print(line)
    print(f"{len(problems)} differences")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Snapshot the corpus**

Both sides must read identical bytes; Claude Code keeps writing to live transcripts.

```bash
mkdir -p ~/.cache/fd-parity
rsync -a --delete --include='*/' --include='*.jsonl' --exclude='*' ~/.claude/projects/ ~/.cache/fd-parity/projects/
```

Add `/home/nathando/.cache/fd-parity` to `TEMP_FILES_SHOULD_BE_REMOVE.MD` with the reason "Go parity corpus snapshot".

- [ ] **Step 4: Run the parity check**

```bash
SCRATCH=/tmp/claude-1000/-home-nathando-Documents-Projects/49f36a17-b0e7-432e-a0e6-cb405cddeea2/scratchpad
RUN=$(date +%s)   # fresh ledgers on both sides every run; nothing is deleted
PYTHONPATH=backend .venv/bin/python scripts/go-parity/python_metrics.py \
  --projects ~/.cache/fd-parity/projects --db "$SCRATCH/py-$RUN.db" --subscription-usd 200 > "$SCRATCH/py-$RUN.json"
make go-build
python3 scripts/go-parity/compare.py --py "$SCRATCH/py-$RUN.json" --fd core/bin/fd \
  --db "$SCRATCH/fd-$RUN.db" --projects ~/.cache/fd-parity/projects --subscription-usd 200
```

Expected: `0 differences`, exit 0. Each difference is either fixed in Go, with a test added to the owning package's test file in the style of that task, or written into the spec's API-compatibility gaps with its reason. Re-run until exit 0.

- [ ] **Step 5: Record what was measured**

Append to `docs/2026-10-01-go-core-binary.md`:

```markdown
## Measured (fill in on the parity run)

| Check | Result |
|---|---|
| Corpus snapshot | <files> files, <GB> |
| Python cold ingest | <s> |
| `fd` cold ingest | <s> |
| `fd` second ingest (nothing new) | <s> |
| Parity, 4 ranges × 4 answers | <n> differences |
| `GET /api/summary?range=all` p50 | <ms> (10 requests, `curl -w '%{time_total}'`) |
```

with the real numbers from Steps 4 and from `fd serve` against `$SCRATCH/fd.db`.

- [ ] **Step 6: Commit**

```bash
git add scripts/go-parity docs/2026-10-01-go-core-binary.md
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "test(core): parity harness against the Python spend metrics"
```

---

### Task 9: History import from the 2026-09-23 dump

**Files:**
- Create: `core/internal/importer/pgcopy.go`
- Create: `core/cmd/fd/importcmd.go`
- Test: `core/internal/importer/pgcopy_test.go`

**Interfaces:**
- Consumes: `ledger.Store`, `ledger.PriceTable`, table `responses` (Task 3); `pricing.Table`, `pricing.Usage` (Task 2); `ingestAll`, `addPaths`, `refuse`, `newFlags`, `parse` (Tasks 1, 6).
- Produces:
  - `func PGCopy(s *ledger.Store, r io.Reader, dryRun bool) (Stats, error)`
  - `type Stats struct` with JSON keys `read`, `billed`, `inserted`, `skipped_key_present`, `skipped_session_on_disk`, `possible_overlap_rows`, `possible_overlap_cost`
  - `var ErrNoCopyHeader error`
  - `fd import --pg-copy FILE|- [--dry-run] [--no-ingest]`

- [ ] **Step 1: Write the failing test**

`core/internal/importer/pgcopy_test.go`:

```go
package importer

import (
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"fdcore/internal/ledger"
)

const header = "COPY public.messages (uuid, session_id, project, model, ts, input_tokens, cache_read, " +
	"cache_create_5m, cache_create_1h, output_tokens, service_tier, message_id, request_id, speed, " +
	"inference_geo, dup_of, dedupe_method, prompt_id, agent_id) FROM stdin;"

// row builds one COPY line; \N is NULL.
func row(uuid, session, model, input, cacheRead, out, msgID, reqID, dupOf, method string) string {
	return strings.Join([]string{uuid, session, `/p`, model, "2026-08-01T00:00:00.000Z", input, cacheRead,
		"0", "0", out, "standard", msgID, reqID, `\N`, `\N`, dupOf, method, `\N`, `\N`}, "\t")
}

func dump(rows ...string) string {
	return "--\n-- PostgreSQL database dump\n--\nSET statement_timeout = 0;\n\n" +
		header + "\n" + strings.Join(rows, "\n") + "\n\\.\n\n"
}

// ledgerWithTranscript holds one transcript response M9 in session s1.
func ledgerWithTranscript(t *testing.T) *ledger.Store {
	t.Helper()
	s, err := ledger.Open(filepath.Join(t.TempDir(), "ledger.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	tx, _ := s.DB.Begin()
	if _, err := ledger.Upsert(tx, ledger.Response{MessageID: "M9", RequestID: "R9", UUID: "t9", SessionID: "s1",
		Model: "claude-opus-5-5", TS: "2026-09-30T00:00:00.000Z", Input: 5, CacheRead: 20000, Output: 1}); err != nil {
		t.Fatal(err)
	}
	tx.Commit()
	return s
}

var sample = dump(
	row("a1", "gone", "claude-opus-5-5", "1", "2", "3", "M1", "R1", `\N`, "exact"),      // inserted
	row("a2", "gone", "claude-opus-5-5", "1", "2", "3", "M1", "R1", "a1", "exact"),      // a repeat in the source
	row("c1", "s1", "claude-opus-5-5", "5", "20000", "1", "M9", "R9", `\N`, "exact"),    // the transcript has it
	row("d1", "gone2", "claude-opus-5-5", "7", "8", "9", `\N`, `\N`, `\N`, "heuristic"), // inserted as uuid:d1
	row("e1", "s1", "claude-opus-5-5", "7", "8", "9", `\N`, `\N`, `\N`, "heuristic"),    // session on disk
	row("f1", "gone3", "claude-opus-5-5", "5", "20000", "1", `\N`, `\N`, `\N`, `\N`),    // inserted, overlaps M9
)

func count(t *testing.T, s *ledger.Store, where string) int {
	t.Helper()
	var n int
	if err := s.DB.QueryRow(`SELECT count(*) FROM responses WHERE ` + where).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func TestImportKeepsTranscriptRowsAndReportsOverlap(t *testing.T) {
	s := ledgerWithTranscript(t)
	st, err := PGCopy(s, strings.NewReader(sample), false)
	if err != nil {
		t.Fatal(err)
	}
	if st.Read != 6 || st.Billed != 5 || st.Inserted != 3 || st.KeyPresent != 1 || st.SessionOnDisk != 1 ||
		st.OverlapRows != 1 {
		t.Fatalf("%+v", st)
	}
	if count(t, s, "source='import'") != 3 || count(t, s, "message_id='uuid:d1' AND dedupe_method='heuristic'") != 1 {
		t.Fatal("imported rows are not marked")
	}
	var sid string
	s.DB.QueryRow(`SELECT session_id FROM responses WHERE message_id='M9'`).Scan(&sid)
	if sid != "s1" || count(t, s, "message_id='M9' AND source='transcript'") != 1 {
		t.Fatal("the transcript row was replaced")
	}
}

func TestImportingTwiceAddsNothing(t *testing.T) {
	s := ledgerWithTranscript(t)
	if _, err := PGCopy(s, strings.NewReader(sample), false); err != nil {
		t.Fatal(err)
	}
	st, err := PGCopy(s, strings.NewReader(sample), false)
	if err != nil {
		t.Fatal(err)
	}
	if st.Inserted != 0 || st.KeyPresent != 4 || count(t, s, "1=1") != 4 {
		t.Fatalf("%+v, rows %d", st, count(t, s, "1=1"))
	}
}

func TestADryRunChangesNothing(t *testing.T) {
	s := ledgerWithTranscript(t)
	st, err := PGCopy(s, strings.NewReader(sample), true)
	if err != nil {
		t.Fatal(err)
	}
	if st.Inserted != 3 || count(t, s, "1=1") != 1 {
		t.Fatalf("%+v, rows %d", st, count(t, s, "1=1"))
	}
}

func TestInputWithoutACopyHeaderIsRefused(t *testing.T) {
	s := ledgerWithTranscript(t)
	if _, err := PGCopy(s, strings.NewReader("SET x = 1;\n"), false); !errors.Is(err, ErrNoCopyHeader) {
		t.Fatalf("err %v", err)
	}
}

func TestCopyEscapes(t *testing.T) {
	if unescape(`\N`) != nil {
		t.Fatal(`\N is not NULL`)
	}
	if got := *unescape(`a\tb\\c\nd`); got != "a\tb\\c\nd" {
		t.Fatalf("%q", got)
	}
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `make go-test GOTEST_ARGS="./internal/importer"`
Expected: FAIL to build, `undefined: PGCopy`.

- [ ] **Step 3: Write the implementation**

`core/internal/importer/pgcopy.go`:

```go
// Package importer brings billed rows from an old FlightDeck ledger into this one.
//
// The source is the text `pg_restore -f - --data-only --table=messages <dump>`
// prints: a COPY header naming the columns, tab-separated rows, then `\.`. No
// Postgres server is involved.
package importer

import (
	"bufio"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"

	"fdcore/internal/ledger"
	"fdcore/internal/pricing"
)

var ErrNoCopyHeader = errors.New("no COPY header: expected `pg_restore -f - --data-only --table=messages` output")

type Stats struct {
	Read          int     `json:"read"`
	Billed        int     `json:"billed"`
	Inserted      int     `json:"inserted"`
	KeyPresent    int     `json:"skipped_key_present"`
	SessionOnDisk int     `json:"skipped_session_on_disk"`
	OverlapRows   int     `json:"possible_overlap_rows"`
	OverlapCost   float64 `json:"possible_overlap_cost"`
}

// PGCopy imports the billed rows (dup_of NULL) of a FlightDeck `messages` table.
// A transcript row always wins: a keyed row is skipped when the key is present,
// and a row without message.id, vouched for only by the heuristic dedupe, is
// imported only for a session with no transcript rows. Imported rows carry
// source='import', so `DELETE FROM responses WHERE source='import'` reverses it.
func PGCopy(s *ledger.Store, r io.Reader, dryRun bool) (Stats, error) {
	var st Stats
	tx, err := s.DB.Begin()
	if err != nil {
		return st, err
	}
	defer tx.Rollback()
	err = readCopy(r, func(row map[string]*string) error {
		st.Read++
		if row["dup_of"] != nil {
			return nil
		}
		st.Billed++
		return importRow(tx, row, &st)
	})
	if err != nil {
		return st, err
	}
	if err := overlap(tx, &st); err != nil {
		return st, err
	}
	if dryRun {
		return st, nil
	}
	return st, tx.Commit()
}

func importRow(tx *sql.Tx, row map[string]*string, st *Stats) error {
	uuid := val(row, "uuid")
	if uuid == "" {
		return nil
	}
	msgID, reqID := val(row, "message_id"), val(row, "request_id")
	if msgID == "" {
		var n int
		if err := tx.QueryRow(`SELECT count(*) FROM responses WHERE session_id=? AND source='transcript'`,
			val(row, "session_id")).Scan(&n); err != nil {
			return err
		}
		if n > 0 {
			st.SessionOnDisk++
			return nil
		}
		msgID = "uuid:" + uuid
	}
	res, err := tx.Exec(`INSERT INTO responses (message_id, request_id, uuid, session_id, project, model, ts,
		input_tokens, cache_read, cache_create_5m, cache_create_1h, output_tokens,
		service_tier, speed, inference_geo, source, dedupe_method)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'import',?) ON CONFLICT(message_id, request_id) DO NOTHING`,
		msgID, reqID, uuid, ptr(row, "session_id"), ptr(row, "project"), ptr(row, "model"), ptr(row, "ts"),
		num(row, "input_tokens"), num(row, "cache_read"), num(row, "cache_create_5m"),
		num(row, "cache_create_1h"), num(row, "output_tokens"),
		ptr(row, "service_tier"), ptr(row, "speed"), ptr(row, "inference_geo"), ptr(row, "dedupe_method"))
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		st.KeyPresent++
	} else {
		st.Inserted++
	}
	return nil
}

type tuple struct {
	model                                string
	input, cacheRead, create5m, create1h int64
}

// overlap counts imported uuid-keyed rows that match a transcript row on the
// input-side token tuple with at least 10k cached tokens, the cross-session key
// scripts/cost-audit/audit_cost.py used. Such a row may bill a response twice;
// it is reported, not removed.
func overlap(tx *sql.Tx, st *Stats) error {
	const cached = `cache_read + cache_create_5m + cache_create_1h >= 10000`
	prices, err := ledger.PriceTable(tx)
	if err != nil {
		return err
	}
	seen := map[tuple]bool{}
	rows, err := tx.Query(`SELECT COALESCE(model,''), input_tokens, cache_read, cache_create_5m, cache_create_1h
		FROM responses WHERE source='transcript' AND ` + cached)
	if err != nil {
		return err
	}
	for rows.Next() {
		var k tuple
		if err := rows.Scan(&k.model, &k.input, &k.cacheRead, &k.create5m, &k.create1h); err != nil {
			rows.Close()
			return err
		}
		seen[k] = true
	}
	rows.Close()
	rows, err = tx.Query(`SELECT COALESCE(model,''), input_tokens, cache_read, cache_create_5m, cache_create_1h,
		output_tokens, COALESCE(ts,'') FROM responses
		WHERE source='import' AND message_id LIKE 'uuid:%' AND ` + cached)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var k tuple
		var out int64
		var ts string
		if err := rows.Scan(&k.model, &k.input, &k.cacheRead, &k.create5m, &k.create1h, &out, &ts); err != nil {
			return err
		}
		if !seen[k] {
			continue
		}
		st.OverlapRows++
		if c, ok := prices.Cost(pricing.Usage{Model: k.model, Input: k.input, CacheRead: k.cacheRead,
			Create5m: k.create5m, Create1h: k.create1h, Out: out, TS: ts}); ok {
			st.OverlapCost += c
		}
	}
	return rows.Err()
}

// readCopy calls each with one row per data line, keyed by the header's column names.
func readCopy(r io.Reader, each func(map[string]*string) error) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	var cols []string
	for sc.Scan() {
		line := sc.Text()
		if cols == nil {
			if strings.HasPrefix(line, "COPY ") {
				cols = copyColumns(line)
			}
			continue // pg_restore's preamble: comments and SET statements
		}
		if line == `\.` {
			return nil
		}
		fields := strings.Split(line, "\t")
		if len(fields) != len(cols) {
			return fmt.Errorf("a row has %d fields, the header %d", len(fields), len(cols))
		}
		row := make(map[string]*string, len(cols))
		for i, f := range fields {
			row[cols[i]] = unescape(f)
		}
		if err := each(row); err != nil {
			return err
		}
	}
	if err := sc.Err(); err != nil {
		return err
	}
	if cols == nil {
		return ErrNoCopyHeader
	}
	return errors.New("the COPY data ends without its \\. terminator")
}

// copyColumns reads `COPY public.messages (uuid, session_id, ...) FROM stdin;`.
func copyColumns(line string) []string {
	open, end := strings.Index(line, "("), strings.LastIndex(line, ")")
	if open < 0 || end < open {
		return nil
	}
	parts := strings.Split(line[open+1:end], ",")
	for i := range parts {
		parts[i] = strings.Trim(strings.TrimSpace(parts[i]), `"`)
	}
	return parts
}

// unescape decodes one COPY text field: \N is NULL, backslash escapes as Postgres writes them.
func unescape(f string) *string {
	if f == `\N` {
		return nil
	}
	if !strings.Contains(f, `\`) {
		return &f
	}
	var b strings.Builder
	for i := 0; i < len(f); i++ {
		c := f[i]
		if c != '\\' || i+1 == len(f) {
			b.WriteByte(c)
			continue
		}
		i++
		switch f[i] {
		case 't':
			b.WriteByte('\t')
		case 'n':
			b.WriteByte('\n')
		case 'r':
			b.WriteByte('\r')
		case 'b':
			b.WriteByte('\b')
		case 'f':
			b.WriteByte('\f')
		case 'v':
			b.WriteByte('\v')
		default:
			b.WriteByte(f[i])
		}
	}
	s := b.String()
	return &s
}

func val(row map[string]*string, k string) string {
	if p := row[k]; p != nil {
		return *p
	}
	return ""
}

func ptr(row map[string]*string, k string) any {
	if v := val(row, k); v != "" {
		return v
	}
	return nil
}

func num(row map[string]*string, k string) int64 {
	n, _ := strconv.ParseInt(val(row, k), 10, 64)
	return n
}
```

`core/cmd/fd/importcmd.go`:

```go
package main

import (
	"errors"
	"fmt"
	"io"
	"os"

	"fdcore/internal/importer"
	"fdcore/internal/ledger"
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
```

- [ ] **Step 4: Run the tests**

Run: `make go-test`
Expected: every package PASSES (5 new importer tests).

- [ ] **Step 5: Dry-run against the real dump and stop for the owner**

```bash
SCRATCH=/tmp/claude-1000/-home-nathando-Documents-Projects/49f36a17-b0e7-432e-a0e6-cb405cddeea2/scratchpad
make go-build
docker exec -i pgcore-17 pg_restore -f - --data-only --table=messages \
  < ~/.flightdeck/backups/flightdeck-2026-09-23-pre-idefix.dump > "$SCRATCH/messages-0923.copy"
core/bin/fd import --pg-copy "$SCRATCH/messages-0923.copy" --dry-run
```

Expected: billed 47,031; the inserted, skipped and overlap counts printed. Report them to the owner together with `core/bin/fd spend --range all` before and the projected total after. Run the import without `--dry-run` only after the owner agrees. Add `$SCRATCH/messages-0923.copy` to `TEMP_FILES_SHOULD_BE_REMOVE.MD`.

- [ ] **Step 6: Commit**

```bash
git add core/internal/importer core/cmd/fd/importcmd.go
git -c user.name="Nathan Do" -c user.email=nathan.do@nakivo.com commit -m "feat(core): fd import from a pg_restore COPY stream"
```

---

## Later slices (one plan each, same ledger and ingest contract)

1. **Turns and activity:** the activity tracker, agent-hours, the turn rows; fills `daily[].agent_hours*`.
2. **Thinking:** `response_timing` from block timestamps; `/api/thinking`.
3. **Quota and burn:** the `claude -p /usage` poller (`--strict-mcp-config --no-session-persistence`), readings, `/api/burn`.
4. **Tasks and logbook:** turn labels.
5. **Search:** `cache.db` (rebuildable), FTS5 with hand-written đ→d normalization.
6. **Runner:** fold fd-runner (`runner/`) into `fd run`; approvals; the hook spool as a local queue.
7. **MCP:** spend and session tools in-process (`fd mcp`).
8. **Packaging:** `go:embed` the built UI, a systemd unit, the binary name, then a thin Electron shell.
