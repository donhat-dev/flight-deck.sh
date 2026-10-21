"""ML-6: every /api/... path the web client requests must exist in
api/openapi/python.json or api/openapi/spend.yaml. The scanning and matching
live in tools/contracts/check_web_routes.py; this test is its conformance
hook. Leftovers go in web_routes_allowlist.txt (shrink-only)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "contracts"))
import check_web_routes as cwr  # noqa: E402


def test_web_routes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "contracts" / "check_web_routes.py"), str(ROOT)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_unresolvable_call_arguments_are_reported():
    """Negative: a helper expanded at its call sites must resolve every one —
    a non-literal argument that cannot be resolved is reported, never
    dropped because another call happened to pass a literal."""
    text = (
        'const k = "does-not-exist";\n'
        'const gitOp = async (kind) => { await rpost(`/api/repo/${kind}`); };\n'
        'gitOp("fetch");\n'
        'gitOp(k);\n'
        'gitOp(makeOp());\n')
    for line, parts in cwr.scan_file(text):
        raws, unres = cwr.build_paths(parts, text)
        paths = {cwr.normalize(r) for r in raws}
        # the literal and the const-bound literal both produce paths
        assert "/api/repo/fetch" in paths
        assert "/api/repo/does-not-exist" in paths
        # the unresolvable call still yields a checked path (param-marked):
        # it is not dropped — and it fails against literal-only routes
        assert "/api/repo/{p}" in paths
        assert not cwr.matches("/api/repo/{p}", ["/api/repo/fetch", "/api/repo/pull"])
        break
    else:
        raise AssertionError("no /api/ literal scanned")


def test_missing_call_argument_is_reported():
    text = (
        'const gitOp = async (kind) => { await rpost(`/api/repo/${kind}`); };\n'
        'gitOp();\n')
    for line, parts in cwr.scan_file(text):
        _, unres = cwr.build_paths(parts, text)
        assert any("missing argument" in w for _, w in unres)
        break
    else:
        raise AssertionError("no /api/ literal scanned")
