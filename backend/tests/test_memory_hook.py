"""The Stop hook that snapshots the memory store after each turn."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def run_hook(env_extra, payload="{}"):
    return subprocess.run(
        [sys.executable, "-m", "flightdeck.memory.hook"],
        cwd=str(BACKEND), input=payload, capture_output=True, text=True,
        timeout=60, env={**__import__("os").environ, **env_extra})


@pytest.fixture()
def configured(tmp_path):
    mem = tmp_path / "projects" / "-proj" / "memory"
    mem.mkdir(parents=True)
    (mem / "alpha.md").write_text("first\n", encoding="utf-8")
    return tmp_path, mem


def test_the_hook_commits_a_changed_store(configured):
    tmp_path, mem = configured
    from flightdeck.memory import vcs
    vcs.init(mem)
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(mem)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is True


def test_the_hook_exits_zero_when_there_is_no_repository(configured):
    _tmp, mem = configured
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(mem)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is False


def test_the_hook_exits_zero_when_the_store_does_not_exist(tmp_path):
    proc = run_hook({"FLIGHTDECK_MEMORY_DIR": str(tmp_path / "nope")})
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["committed"] is False
