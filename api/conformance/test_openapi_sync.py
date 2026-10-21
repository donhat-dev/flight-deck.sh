"""The generated OpenAPI stays in sync: python.json is the current output of
tools/contracts/gen_python_openapi.py, and every route spend.yaml marks for
the Python server exists in python.json's paths.
"""
import json
import subprocess
import sys

from conftest import GEN_OPENAPI, PYTHON_JSON, shared_paths

_METHODS = {"get", "put", "post", "delete", "patch"}


def test_python_json_is_up_to_date(tmp_path):
    out = tmp_path / "python.json"
    subprocess.run([sys.executable, str(GEN_OPENAPI), str(out)],
                   check=True, capture_output=True, text=True, timeout=120)
    assert out.read_text() == PYTHON_JSON.read_text(), (
        "api/openapi/python.json is stale — regenerate with "
        "`tools/contracts/gen_python_openapi.py`")


def test_spend_yaml_paths_served_by_python(spend_spec, python_spec):
    paths = python_spec["paths"]
    missing = []
    for path, item in spend_spec["paths"].items():
        for method, op in item.items():
            if method not in _METHODS:
                continue
            if "python" not in (op.get("x-servers") or []):
                continue
            if path not in paths or method not in paths[path]:
                missing.append(f"{method.upper()} {path}")
    assert not missing, (
        f"spend.yaml routes the Python server does not have: {missing}")
