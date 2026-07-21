"""FastAPI app assembly: config + DB init, shared state, router wiring.

The runtime/infra (snapshot cache, ingest, watcher + poll threads, the SSE
`asyncio.Event`, the lifespan) lives in `flightdeck.runtime`; the 32 HTTP
endpoints live in `flightdeck.routers.*` (mirroring the existing hub/ systems/
agui/ router pattern). This file just wires them together.

Single process / 1 worker by design (docs/host-stack-migration.md).
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from flightdeck import config, db, runtime
from flightdeck.hub import credentials
from flightdeck.hub.nodes import load as hub_load
from flightdeck.routers import charts, core, diff, hub, sessions, stream
from flightdeck.systems import containers as sys_containers
from flightdeck.systems import mcp as sys_mcp
from flightdeck.systems import skills as sys_skills
from flightdeck.agui import routes as agui_routes


def create_app() -> FastAPI:
    cfg = config.load(os.environ.get("TOKEN_AUDIT_CONFIG", "config.toml"))
    db.configure(cfg)   # pick engine: PostgreSQL if database_url set, else SQLite

    # Ensure schema exists (creates tables), then close that init connection.
    conn = db.connect(cfg["db_path"])
    conn.close()

    # Dedicated long-lived WRITE connection, used ONLY by the watcher/ingest
    # (serialized by the runtime lock). SQLite: WAL + bounded busy-timeout so
    # per-request readers never block on the writer. PostgreSQL: a dedicated
    # connection with explicit commits. (Engine details live in db.py.)
    write_conn = db.open_write(cfg["db_path"])

    # Integration Hub: register node types (registry side-effect import) and
    # ensure the credentials table exists, on the same write connection used
    # everywhere else. Flows are files, not DB rows -- flows_dir below.
    hub_load.load_all()
    credentials.init(write_conn)
    flows_dir = os.environ.get("TOKEN_AUDIT_FLOWS_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "flows")

    app = FastAPI(title="FlightDeck - Claude Code management deck",
                  lifespan=runtime.lifespan)

    # Shared state the endpoint routers read via request.app.state.*
    app.state.cfg = cfg
    app.state.write_conn = write_conn
    app.state.flows_dir = flows_dir
    app.state.rtk_savings = 0.0
    app.state.snap = None
    app.state.graph_cache = {}
    app.state.runtime = runtime.Runtime(app, cfg, write_conn)

    # Endpoint routers (extracted from the old create_app mega-factory).
    app.include_router(core.router)
    app.include_router(sessions.router)
    app.include_router(charts.router)
    app.include_router(diff.router)
    app.include_router(hub.router)
    app.include_router(stream.router)
    # Systems views (Comms / Manuals / Hangar) + AG-UI Relay stream: already
    # their own routers, read-only surface.
    app.include_router(sys_mcp.router)
    app.include_router(sys_skills.router)
    app.include_router(sys_containers.router)
    app.include_router(agui_routes.router)

    # Static SPA mount MUST stay LAST: it catches every non-/api route to serve
    # the built frontend.
    dist = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    if os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="static")

    return app


if os.environ.get("TOKEN_AUDIT_CONFIG") or os.path.exists("config.toml"):
    app = create_app()
else:
    app = None
