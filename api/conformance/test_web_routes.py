"""ML-6: every /api/... path the web client requests must exist in
api/openapi/python.json or api/openapi/spend.yaml. The scanning and matching
live in tools/contracts/check_web_routes.py; this test is its conformance
hook. Leftovers go in web_routes_allowlist.txt (shrink-only)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_web_routes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "contracts" / "check_web_routes.py"), str(ROOT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
