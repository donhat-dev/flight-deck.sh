# FlightDeck on Odoo 19

The experiment stack behind `docs/2026-09-08-odoo-base-app-experiment.md`. Its own
project, database and port: it shares nothing with the NAKIVO tracks in
`platform/odoo-dev`.

```bash
cd docker/odoo19
docker compose up -d
cp .env.example .env                          # then fill in the two role passwords
docker exec flightdeck-odoo19 /entrypoint.sh odoo -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo -i flight_deck --stop-after-init
docker exec -i flightdeck-odoo19 /entrypoint.sh odoo shell -c /etc/odoo/odoo.conf \
    -d flightdeck_odoo --no-http < ../../odoo/migration/import_ledger.py
```

| | |
|---|---|
| URL | http://localhost:8029, `admin` / `admin` |
| Database | `flightdeck_odoo` on `pgcore-17`, owned by the `fdodoo` role |
| Ledger | read through the `fdreader` role, which holds SELECT and nothing else |
| Module source | `../../odoo/flight_deck`, mounted read only |
| Artifacts | `~/.flightdeck/treasures`, mounted read only |

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
