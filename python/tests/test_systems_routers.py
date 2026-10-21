"""Route tests for the routers moved out of agui/ and systems/ (SP-5).

Each test builds the app against a temp config/DB like test_server.py and
pins the path-override env vars (or the docker helpers) so nothing touches
the real socket, registries or network.
"""
import json

from fastapi.testclient import TestClient


def _build_app(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    proj = tmp_path / "projects"
    proj.mkdir()
    cfg.write_text(
        'subscription_monthly_usd = 0.0\n'
        f'projects_dir = "{proj.as_posix()}"\n'
        f'db_path = "{(tmp_path / "audit.db").as_posix()}"\n')
    monkeypatch.setenv("TOKEN_AUDIT_CONFIG", str(cfg))
    from flightdeck import server
    return server.create_app()


# --- routers/agui.py ---------------------------------------------------------

def test_agui_sessions(tmp_path, monkeypatch):
    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/agui/sessions")
        assert r.status_code == 200
        body = r.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)


def test_agui_run_replay_without_session(tmp_path, monkeypatch):
    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/agui/run", json={
            "forwardedProps": {"mode": "replay", "speed": 0}})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "RUN_ERROR" in r.text
        assert "no sessionId" in r.text


# --- routers/systems_containers.py -------------------------------------------

def _fake_container(name, project=None, state="running"):
    return {
        "Id": name * 12,
        "Names": [f"/{name}"],
        "Image": "img:latest",
        "State": state,
        "Status": "Up 2 hours",
        "Created": 1700000000,
        "Ports": [],
        "Labels": ({"com.docker.compose.project": project,
                    "com.docker.compose.service": name}
                   if project else {}),
    }


def test_containers_overview_mocked_socket(tmp_path, monkeypatch):
    from flightdeck.routers import systems_containers as rc
    monkeypatch.setattr(rc, "_resolve_sock", lambda: "/fake/docker.sock")

    def fake_get(sock, path):
        if path == "/containers/json?all=true":
            return [_fake_container("web", project="site"),
                    _fake_container("lonely")]
        if path == "/version":
            return {"Version": "25.0.0"}
        raise AssertionError(f"unexpected docker path {path}")

    monkeypatch.setattr(rc, "_get", fake_get)

    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/systems/containers")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["socket"] == "/fake/docker.sock"
        assert body["summary"]["total"] == 2
        assert body["summary"]["docker_version"] == "25.0.0"
        projects = {p["project"]: p for p in body["projects"]}
        assert projects["site"]["containers"][0]["name"] == "web"
        assert projects["standalone"]["standalone"] is True


def test_container_stats_rejects_bad_id(tmp_path, monkeypatch):
    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/systems/containers/not-a-real-id/stats")
        assert r.status_code == 400
        assert r.json() == {"detail": "invalid container id"}


# --- routers/systems_mcp.py --------------------------------------------------

def test_mcp_overview_registered_server(tmp_path, monkeypatch):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {"alpha": {"command": "alpha-bin", "args": ["--serve"]}}}))
    monkeypatch.setenv("TOKEN_AUDIT_CLAUDE_JSON", str(claude_json))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("FLIGHTDECK_WORKSPACE", str(ws))

    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/systems/mcp")
        assert r.status_code == 200
        body = r.json()
        names = [s["name"] for s in body["servers"]]
        assert "alpha" in names
        alpha = next(s for s in body["servers"] if s["name"] == "alpha")
        assert alpha["status"] == "idle"      # registered, no usage rows
        assert alpha["scope"] == "global"
        assert body["totals"]["registered"] >= 1


# --- routers/systems_skills.py ------------------------------------------------

def test_skills_overview_personal_skill(tmp_path, monkeypatch):
    personal = tmp_path / "personal" / "myskill"
    personal.mkdir(parents=True)
    (personal / "SKILL.md").write_text(
        "---\nname: myskill\ndescription: does a thing\n---\nbody\n")
    monkeypatch.setenv("TOKEN_AUDIT_SKILLS_DIR", str(tmp_path / "personal"))
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("TOKEN_AUDIT_PLUGINS_DIR", str(plugins))
    ws = tmp_path / "ws"
    ws.mkdir()
    from flightdeck import repodiff
    monkeypatch.setattr(repodiff, "WORKSPACE", str(ws))

    app = _build_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = client.get("/api/systems/skills")
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["total"] == 1
        skill = body["skills"][0]
        assert skill["name"] == "myskill"
        assert skill["source"] == "personal"
        assert skill["description"] == "does a thing"
        assert skill["broken"] is False
