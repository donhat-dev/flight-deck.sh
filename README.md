# Flightdoo — FlightDeck on Odoo 19

Flightdoo tracks and manages harness productivity on Odoo 19: cost, agent
work-hours, tasks and their labels, plus sessions and orchestration. This branch
holds the Odoo addons and themes, migration scripts, `fd-runner` and Docker stack.
FlightDeck, the original FastAPI + React app, is maintained on `main`.

## Layout

- `odoo/flight_deck` — the main addon, mounted read-only at `/mnt/flightdeck-addons`
- `odoo/flight_deck_workflow` — Workflow Studio steps: Ask Agent, Start Session
- `odoo/flightdeck_theme`, `odoo/flightdeck_theme_light` — the deck themes
- `odoo/migration` — one-shot import/backfill scripts run through `odoo shell`
- `odoo/tests` — Odoo-free unit tests (`make test`)
- `runner/` — `fd-runner`, the Go control process inside the Odoo container
- `docker/odoo19` — Dockerfile, compose file and `odoo.conf`; see its README
- `vendor/open-workflow-studio` — Workflow Studio, a git submodule on branch
  `19.0` pinned at `3eb9c30`
- `docs/` — design notes and briefs for this branch

## Setup

The vendor addons under `docker/odoo19/addons/` are not in this repository:
provision them per [docker/odoo19/README.md](docker/odoo19/README.md) before
the first startup, or the `addons_path` mounts come up empty.

```bash
git submodule update --init vendor/open-workflow-studio
cp docker/odoo19/.env.example docker/odoo19/.env   # role passwords, FD_UID=$(id -u)
make build up
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo -i flight_deck --stop-after-init
```

URL http://localhost:8029, `admin` / `admin`. Database `flightdeck_odoo` on
`pgcore-17`. Full notes — `FD_UID`, entrypoint, first-install repair — are in
[docker/odoo19/README.md](docker/odoo19/README.md).

## Workflow Studio source

`compose.yml` mounts five Workflow Studio addons module by module from
`WFS_SRC`, which defaults to the `vendor/open-workflow-studio` submodule — a
pinned snapshot of `github.com/donhat-dev/open-workflow-studio` branch `19.0`.
To develop against a live checkout instead, set `WFS_SRC` in
`docker/odoo19/.env` to an absolute path, for example
`/home/<you>/Documents/Projects/repos/open-workflow-studio`. Relative paths
resolve from `docker/odoo19`, so `../../vendor/open-workflow-studio` is the
in-tree default. The module-by-module mounts keep the separate pinned
`queue_job` under `docker/odoo19/addons/` authoritative on `addons_path`;
`workflow_studio_ai` is intentionally excluded: it depends on `ai`, which Odoo
19 CE does not have.

## Tests

`make test` runs the Odoo-free `odoo/tests` suite; it needs a Python with
pytest installed — set `PYTHON` to choose the interpreter, e.g.
`make test PYTHON=/path/to/.venv/bin/python`. `make runner-test` runs
`go test ./...` for `runner/` inside the `golang:1.26-alpine` image, so no Go
toolchain is needed on the host.

## Make targets

| Target | What it does |
| --- | --- |
| `make config` | `docker compose config --quiet` (env resolution check) |
| `make build` | build the `flightdeck-odoo19:local` image |
| `make up` | `docker compose up -d` |
| `make logs` | follow the stack logs |
| `make test` | the Odoo-free `odoo/tests` suite |
| `make runner-test` | `go test ./...` for `runner/` in the golang:1.26-alpine image |
