import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "interactive_ui_picker.py"

ROLE_FILES = {
    "canvas_ui": "canvas-ui.json",
    "motion_3d": "motion-3d.json",
    "color_art": "color-art.json",
}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def make_record(role: str, index: int) -> dict:
    prefix = {"canvas_ui": "canvas", "motion_3d": "motion", "color_art": "art"}[role]
    return {
        "id": f"{prefix}-{index + 1:03d}",
        "role": role,
        "title": f"{role} reference {index + 1}",
        "creator": f"creator-{role}-{index + 1}",
        "canonical_url": f"https://example.test/{role}/{index + 1}",
        "platform": f"platform-{index % 4}",
        "category": f"category-{index % 4}",
        "media_type": "sequence",
        "captured_at": "2026-08-12",
        "availability": "ok",
    }


def write_valid_manifests(tmp_path: Path) -> Path:
    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    for role, filename in ROLE_FILES.items():
        records = [make_record(role, index) for index in range(24)]
        (candidate_dir / filename).write_text(json.dumps(records), encoding="utf-8")
    return candidate_dir


def load_records(candidate_dir: Path, role: str) -> list[dict]:
    return json.loads((candidate_dir / ROLE_FILES[role]).read_text(encoding="utf-8"))


def save_records(candidate_dir: Path, role: str, records: list[dict]) -> None:
    (candidate_dir / ROLE_FILES[role]).write_text(json.dumps(records), encoding="utf-8")


def write_mutated_manifests(tmp_path: Path, mutation: str) -> Path:
    candidate_dir = write_valid_manifests(tmp_path)
    canvas = load_records(candidate_dir, "canvas_ui")
    if mutation == "too_few":
        canvas.pop()
    elif mutation == "platform_over_cap":
        for item in canvas[:11]:
            item["platform"] = "one-platform"
    elif mutation == "creator_over_cap":
        for item in canvas[:3]:
            item["creator"] = "one-creator"
    elif mutation == "too_few_categories":
        for item in canvas:
            item["category"] = "one-category"
    elif mutation == "cross_pool_duplicate":
        art = load_records(candidate_dir, "color_art")
        art[0]["canonical_url"] = canvas[0]["canonical_url"]
        save_records(candidate_dir, "color_art", art)
    elif mutation == "aesthetic_score":
        canvas[0]["aesthetic_score"] = 10
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    save_records(candidate_dir, "canvas_ui", canvas)
    return candidate_dir


def test_draw_is_reproducible(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    first = run_cli("draw", "--candidate-dir", str(candidate_dir), "--seed", "a1b2c3")
    second = run_cli("draw", "--candidate-dir", str(candidate_dir), "--seed", "a1b2c3")
    assert first.returncode == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert set(json.loads(first.stdout)["selected"]) == {
        "canvas_ui", "motion_3d", "color_art"
    }


@pytest.mark.parametrize("mutation,error_code", [
    ("too_few", "pool_too_small"),
    ("platform_over_cap", "platform_cap_exceeded"),
    ("creator_over_cap", "creator_cap_exceeded"),
    ("too_few_categories", "category_floor_not_met"),
    ("cross_pool_duplicate", "canonical_url_reused"),
    ("aesthetic_score", "forbidden_pre_draw_field"),
])
def test_validation_rejects_contract_violations(tmp_path, mutation, error_code):
    candidate_dir = write_mutated_manifests(tmp_path, mutation)
    result = run_cli("validate", "--candidate-dir", str(candidate_dir))
    assert result.returncode == 2
    assert error_code in {item["code"] for item in json.loads(result.stdout)["errors"]}


def test_draw_uses_next_hashed_candidate_for_permitted_skip(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    initial = json.loads(run_cli(
        "draw", "--candidate-dir", str(candidate_dir), "--seed", "fixed"
    ).stdout)
    skipped_id = initial["selected"]["canvas_ui"]["id"]
    skip_file = tmp_path / "skips.json"
    skip_file.write_text(json.dumps({skipped_id: "inaccessible"}))
    changed = run_cli(
        "draw", "--candidate-dir", str(candidate_dir), "--seed", "fixed",
        "--skip-file", str(skip_file),
    )
    payload = json.loads(changed.stdout)
    assert changed.returncode == 0
    assert payload["selected"]["canvas_ui"]["id"] != skipped_id
    assert payload["skipped"] == [{"id": skipped_id, "reason": "inaccessible"}]


def test_draw_rejects_subjective_skip_reason(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    skip_file = tmp_path / "skips.json"
    skip_file.write_text(json.dumps({"canvas-001": "not_pretty"}))
    result = run_cli(
        "draw", "--candidate-dir", str(candidate_dir), "--seed", "fixed",
        "--skip-file", str(skip_file),
    )
    assert result.returncode == 2
    assert "invalid_skip_reason" in result.stdout


def test_draw_requires_24_available_candidates_per_role(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    canvas = load_records(candidate_dir, "canvas_ui")
    for item in canvas[1:]:
        item["availability"] = "inaccessible"
    save_records(candidate_dir, "canvas_ui", canvas)

    result = run_cli("draw", "--candidate-dir", str(candidate_dir), "--seed", "fixed")

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert {
        item["code"] for item in payload["errors"]
    } >= {"pool_too_small"}


def test_validation_reports_malformed_field_types_and_other_record_errors(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    canvas = load_records(candidate_dir, "canvas_ui")
    canvas[0]["id"] = ["not", "a", "string"]
    canvas[0]["platform"] = ["not", "a", "string"]
    canvas[0]["availability"] = {"not": "a string"}
    canvas[1].pop("title")
    canvas[1]["creator"] = {"not": "a string"}
    save_records(candidate_dir, "canvas_ui", canvas)

    result = run_cli("validate", "--candidate-dir", str(candidate_dir))

    payload = json.loads(result.stdout)
    codes = [item["code"] for item in payload["errors"]]
    assert result.returncode == 2
    assert codes.count("invalid_field_type") >= 4
    assert "required_field_missing" in codes


def test_validate_pool_loads_only_the_requested_manifest(tmp_path):
    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    records = [make_record("canvas_ui", index) for index in range(24)]
    (candidate_dir / ROLE_FILES["canvas_ui"]).write_text(json.dumps(records), encoding="utf-8")

    result = run_cli(
        "validate-pool", "--candidate-dir", str(candidate_dir), "--role", "canvas_ui"
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["errors"] == []
    assert set(payload["checksums"]) == {"canvas_ui"}


def test_validate_pool_accepts_exact_manifest_path_interface(tmp_path):
    candidate_dir = write_valid_manifests(tmp_path)
    manifest = candidate_dir / ROLE_FILES["canvas_ui"]

    result = run_cli("validate-pool", "--path", str(manifest), "--role", "canvas_ui")

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["errors"] == []
    assert set(payload["checksums"]) == {"canvas_ui"}
