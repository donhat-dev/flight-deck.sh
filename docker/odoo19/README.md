# FlightDeck on Odoo 19

The experiment stack behind `docs/2026-09-08-odoo-base-app-experiment.md`. Its own
project, database and port: it shares nothing with the NAKIVO tracks in
`platform/odoo-dev`.

```bash
cd docker/odoo19
cp .env.example .env         # role passwords, and FD_UID=$(id -u)
docker compose up -d
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo -i flight_deck --stop-after-init
docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo --no-http < ../../clients/odoo/migration/import_ledger.py
```

| | |
|---|---|
| URL | http://localhost:8029, `admin` / `admin` |
| Database | `flightdeck_odoo` on `pgcore-17`, owned by the `fdodoo` role |
| Ledger | read through the `fdreader` role, which holds SELECT and nothing else |
| Module source | `../../clients/odoo/flight_deck`, mounted read only |
| Artifacts | `~/.flightdeck/treasures`, mounted read only |
| Transcripts | `~/.claude/projects`, mounted read only |
| Runs as | `FD_UID` from `.env` with group 101, so it reads transcripts as their owner |

`FD_UID` is why the transcripts are readable at all. claude writes every session and
every agent file 0600, and a file created that way never picks up a default ACL: the
mask follows the group bits. Reading them as the uid that owns them needs nothing
maintained, and the mount is read-only so the identity cannot write there. The data
directory stays group 101, which is why the image opens it to that group.

If the first install fails on a module error, fix it and then run `-u base` before
anything else. Odoo installs its `auto_install` modules in the last step of a load, so a
run that aborts earlier leaves them out and no later `-i flight_deck` brings them back. The
symptom is not an error in the log: the backend works while every other page renders
unstyled with "Style error. The style compilation failed.", because `web.assets_frontend`
cannot compile without `html_editor`.

Go through `/entrypoint.sh`, not `odoo` directly: the credentials live in `.env`, and it is
the entrypoint that turns them into `--db_user` and `--db_password`. Calling `odoo` on its
own reads only the config file, which carries no password, and fails to connect.

The import is idempotent: a second run adds only what the ledger gained since the first.

After editing the module, `-u flight_deck --stop-after-init` and restart the container;
the running process keeps the old registry and the old assets otherwise.

## Third-party addons

`addons/` carries other people's modules and is not in this repository: it is mounted
read-only at `/mnt/vendor-addons` and named last in `addons_path`. What is in it comes
from one pinned source, so refilling it is a clone and a copy:

    git clone --depth 1 --branch 19.0 --filter=blob:none --sparse \
        https://github.com/muk-it/odoo-modules.git
    git -C odoo-modules sparse-checkout set muk_web_theme muk_web_group muk_web_chatter \
        muk_web_dialog muk_web_appsbar muk_web_colors muk_web_refresh \
        muk_web_utils muk_ai muk_mcp

The job queue comes from a second source, the same way:

    git clone --depth 1 --branch 19.0 --filter=blob:none --sparse \
        https://github.com/OCA/queue.git
    git -C queue sparse-checkout set queue_job

`queue_job` 19.0.2.1.3, LGPL-3, repository `OCA/queue` branch `19.0` at commit `bc1d885`.
It is loaded server-wide (`server_wide_modules` in `odoo.conf`) because its runner is a
thread it patches into the server, which only happens if the module is loaded before any
database is. `openupgradelib` is its python dependency and is installed in the image. With
`workers = 0` the runner runs in the threaded server, which is why the websocket the live
transcript rides on stays on the main port and Caddy needs no split route.

MuK backend theme, LGPL-3, repository `muk-it/odoo-modules` branch `19.0` at commit
`8d2f831`. `muk_web_theme` 19.0.1.4.9 plus the six modules it depends on. Installing it
also pulls in stock `base_automation`, which `muk_web_refresh` needs, and the
`auto_install` modules that follow from it; uninstalling the theme leaves those installed.

To remove it: `button_immediate_uninstall` on `muk_web_theme`, then drop the
`/mnt/vendor-addons` entry from `odoo.conf` and the `./addons` mount from `compose.yml`.

## Workflow Studio

Workflow Studio's modules (`workflow_engine`, `workflow_graph`, `workflow_studio`, the
connectors, `workflow_studio_queue_job` and the `harness_*` modules) come from their own
repository, `github.com/donhat-dev/open-workflow-studio`, cloned in the workspace at
`repos/open-workflow-studio` on branch `19.0` (the Odoo 19 port; `main` is 18.0). Unlike
`addons/`, that clone is where changes to it are made, so it is mounted as it is rather
than copied: set `WFS_SRC` in `.env` to use another checkout. The whole repository is one
addons path at `/mnt/wfs-addons`, last in `addons_path`; it keeps no `queue_job` of its
own, so the one in `addons/` is the only one. `workflow_studio_ai` sits on the path but is
not installed: it depends on `ai`, which Odoo 19 CE does not have.

`flight_ai_chat` (the FlightDeck chat on MuK AI) lives in `clients/odoo/` with the other
FlightDeck modules; it needs `muk_ai`, `muk_mcp` and `muk_web_utils` from `addons/`,
pinned in `addons/MUK_PIN.txt`.

A person needs one of the Workflow Studio groups (Settings › Users) to see its menu.
