# FlightDeck core (`fd`)

`fd` is the FlightDeck core for one host: it reads Claude Code transcripts into
a durable spend ledger and answers from it, as a CLI or as a daemon
(`fd serve`).

## Layout

- `core/` — the Go module (`fdcore`); the binary builds to `core/bin/fd`
- `Makefile` — Docker-based build/test targets; no Go toolchain needed on the host

## Build and test

Builds and tests run in the official `golang:1.26-alpine` image as the calling
user, with module and build caches persisted under `~/.cache/fd-go`:

    make            # default: go-build -> core/bin/fd
    make test       # go test ./... (GOTEST_ARGS narrows it)

## Running

The ledger is a single SQLite file, separate from any other store; it defaults
to `~/.flightdeck/fd/ledger.db`. Paths are flags or environment variables:

- `FD_DB` / `--db` — ledger file (default `~/.flightdeck/fd/ledger.db`)
- `FD_PROJECTS` / `--projects` — transcript projects directory
- `FD_ADDR` — `fd serve` listen address (default `127.0.0.1:8011`; the Python
  backend holds `:8010`)

`fd serve` exposes the API only by default. `--ui` can serve a separately built
UI directory; this branch does not bundle frontend sources. Every command
accepts `--json` for one JSON document on stdout. No Python or Postgres runtime
is needed.

## Importing an old ledger

`fd import --pg-copy <file>` loads billed rows from an offline Postgres dump:
produce the file with `pg_restore -f - --data-only --table=messages` (or `-` to
read stdin). This reads a dump file only; fd never connects to a live database.
`--dry-run` reports what would be imported; `--no-ingest` skips the transcript
sweep that normally runs first.
