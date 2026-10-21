# FlightDeck repository in layers

Feature: FlightDeck repository in layers
Goal: one `main` branch whose folders say what each part is — the Go core, the Python stack beside it, the servers, the clients — so the Go core can grow while the Python stack keeps running.
Now: one repository, folders by history (`backend/`, `frontend/`, `core/`, `runner/`, `odoo/`); until 2026-10-21 the parts lived on separate branches (`main`, `go`, `odoo-19-experiment`, `go-core-slice1`), now merged into `main` (70d5902).
Direction: top level by runtime and role — `go/` (core, server, runner, commands), `python/` (the FastAPI stack), `clients/` (web, odoo), `api/` (contracts between servers and clients), deploy files where they run.
Constraints: Flightdoo (`flightdeck-odoo19`, OWS inside it) and the FlightDeck service (`flightdeck.service`, port 8010) keep running after a remount/restart; the Go core opens no Postgres connection; Odoo modules may keep their duplicated logic for now; `docker/odoo19` stays where it is in this round (other sessions run probes from it).
Done when: FL-1..FL-6 hold.
Not now: porting Python features to Go; removing the Python stack; deduplicating the Odoo modules; moving `docker/`.
Refs: `docs/2026-10-01-go-core-binary.md` (Go core decisions), evidence `devtools_mcp_trace/2026-10-21-flightdeck-layout/`.

## Done when

- FL-1 Top-level folders: `go/`, `python/`, `clients/web/`, `clients/odoo/`, `api/`, plus `docker/`, `deploy/`, `docs/`, `scripts/`; no `backend/`, `frontend/`, `core/`, `runner/`, `odoo/` left.
- FL-2 `go/` is one Go module with the core packages (ledger, ingest, pricing, metrics, importer), the HTTP server as its own package, the runner, and one `cmd/` per binary; `make go-test` and `make go-build` pass; no Go package imports a Postgres driver.
- FL-3 The Python stack runs from `python/`: its tests pass, `flightdeck.service` is reinstalled from the new path and answers on 8010.
- FL-4 The React client builds from `clients/web/`, and the Python and Go servers find its build output.
- FL-5 Flightdoo mounts `clients/odoo/` and starts with every module loaded; the fd-runner image build reads `go/`.
- FL-6 `api/` holds the contracts both servers serve and the clients read (spend API, CLI JSON contract, runner spool protocol), written from the code as it is.

## Definitions

- **Core**: the Go code that owns the ledger on one host (ingest, ledger, pricing, metrics, import). No HTTP, no UI.
- **Server**: a process that serves the API (and the web client's files) to clients: `fd serve` (Go) or the FastAPI app (Python).
- **Runner**: the process that starts and drives agent sessions from a spool (`fd-runner`).
- **Client**: what a person uses: the React web app, the Flightdoo Odoo modules.

## Decisions

- Chose to merge every branch into `main` before moving folders, because a move on four diverging branches would multiply the conflicts; `odoo-19-experiment` and `go` were merged with `-s ours` (their files were older or identical), the uncommitted resource-manager work went to `wip/resource-manager`, not `main`, because it is untested and `main` runs live.
