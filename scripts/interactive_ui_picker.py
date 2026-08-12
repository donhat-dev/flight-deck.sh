#!/usr/bin/env python3
"""Validate and deterministically draw interactive UI research references."""

import argparse
import hashlib
import json
import secrets
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROLES = {
    "canvas_ui": "canvas-ui.json",
    "motion_3d": "motion-3d.json",
    "color_art": "color-art.json",
}
REQUIRED_FIELDS = {
    "id", "role", "title", "creator", "canonical_url", "platform",
    "category", "media_type", "captured_at", "availability",
}
FORBIDDEN_PRE_DRAW_FIELDS = {
    "aesthetic_score", "usefulness_score", "model_preference", "vision_notes",
}
ALLOWED_SKIP_REASONS = {
    "inaccessible", "insufficient_media", "duplicate", "wrong_role",
}
ALLOWED_AVAILABILITY = {"ok", *ALLOWED_SKIP_REASONS}
MIN_POOL_SIZE = 24
MAX_PER_PLATFORM = 10
MAX_PER_CREATOR = 2
MIN_CATEGORIES = 4


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def checksum(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def order_key(seed: str, role: str, candidate_id: str) -> str:
    material = f"{seed}\0{role}\0{candidate_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def ordered_candidates(records: list[dict[str, Any]], seed: str, role: str) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: order_key(seed, role, item["id"]))


def error(code: str, message: str, **details: object) -> dict[str, object]:
    return {"code": code, "message": message, **details}


def load_pools(candidate_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, object]]]:
    pools: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, object]] = []
    for role, filename in ROLES.items():
        path = candidate_dir / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            errors.append(error("pool_file_missing", "Candidate manifest is missing.", role=role, file=filename))
            pools[role] = []
            continue
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(error("pool_file_invalid", "Candidate manifest cannot be read as JSON.", role=role, file=filename, detail=str(exc)))
            pools[role] = []
            continue
        if not isinstance(value, list):
            errors.append(error("pool_not_array", "Candidate manifest must contain a JSON array.", role=role, file=filename))
            pools[role] = []
            continue
        pools[role] = [item for item in value if isinstance(item, dict)]
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(error("candidate_not_object", "Candidate entries must be JSON objects.", role=role, index=index))
    return pools, errors


def validate_pool(role: str, records: list[dict[str, Any]]) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    if len(records) < MIN_POOL_SIZE:
        errors.append(error("pool_too_small", "Candidate pool is smaller than the required minimum.", role=role, minimum=MIN_POOL_SIZE, actual=len(records)))

    ids: set[str] = set()
    platforms: Counter[str] = Counter()
    creators: Counter[str] = Counter()
    categories: set[str] = set()
    for index, item in enumerate(records):
        missing = sorted(REQUIRED_FIELDS - item.keys())
        if missing:
            errors.append(error("required_field_missing", "Candidate is missing required fields.", role=role, index=index, fields=missing))
            continue
        forbidden = sorted(FORBIDDEN_PRE_DRAW_FIELDS & item.keys())
        if forbidden:
            errors.append(error("forbidden_pre_draw_field", "Candidate contains a forbidden pre-draw field.", role=role, id=item["id"], fields=forbidden))
        if item["role"] != role:
            errors.append(error("role_mismatch", "Candidate role does not match its manifest.", role=role, id=item["id"], actual=item["role"]))
        if item["availability"] not in ALLOWED_AVAILABILITY:
            errors.append(error("invalid_availability", "Candidate availability is not permitted.", role=role, id=item["id"], availability=item["availability"]))
        candidate_id = item["id"]
        if candidate_id in ids:
            errors.append(error("candidate_id_reused", "Candidate IDs must be unique within a pool.", role=role, id=candidate_id))
        ids.add(candidate_id)
        platforms[item["platform"]] += 1
        creators[item["creator"]] += 1
        categories.add(item["category"])

    for platform, count in sorted(platforms.items()):
        if count > MAX_PER_PLATFORM:
            errors.append(error("platform_cap_exceeded", "A platform exceeds the candidate cap.", role=role, platform=platform, maximum=MAX_PER_PLATFORM, actual=count))
    for creator, count in sorted(creators.items()):
        if count > MAX_PER_CREATOR:
            errors.append(error("creator_cap_exceeded", "A creator exceeds the candidate cap.", role=role, creator=creator, maximum=MAX_PER_CREATOR, actual=count))
    if len(categories) < MIN_CATEGORIES:
        errors.append(error("category_floor_not_met", "Candidate pool has too few categories.", role=role, minimum=MIN_CATEGORIES, actual=len(categories)))
    return errors


def validate(candidate_dir: Path, roles: set[str] | None = None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, object]]:
    pools, errors = load_pools(candidate_dir)
    active_roles = roles or set(ROLES)
    for role in ROLES:
        if role in active_roles:
            errors.extend(validate_pool(role, pools[role]))

    if active_roles == set(ROLES):
        seen_urls: dict[str, tuple[str, str]] = {}
        for role in ROLES:
            for item in pools[role]:
                url = item.get("canonical_url")
                candidate_id = item.get("id")
                if not isinstance(url, str) or not isinstance(candidate_id, str):
                    continue
                if url in seen_urls:
                    first_role, first_id = seen_urls[url]
                    errors.append(error("canonical_url_reused", "Canonical URLs must be unique across all pools.", canonical_url=url, first_role=first_role, first_id=first_id, role=role, id=candidate_id))
                else:
                    seen_urls[url] = (role, candidate_id)

    checksums = {role: checksum(pools[role]) for role in ROLES if role in active_roles}
    report: dict[str, object] = {
        "valid": not errors,
        "errors": errors,
        "checksums": checksums,
    }
    return pools, report


def load_skip_map(path: Path | None) -> tuple[dict[str, str], list[dict[str, object]]]:
    if path is None:
        return {}, []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [error("skip_file_invalid", "Skip file cannot be read as a JSON object.", detail=str(exc))]
    if not isinstance(value, dict):
        return {}, [error("skip_file_invalid", "Skip file must contain a JSON object.")]
    skip_map: dict[str, str] = {}
    errors: list[dict[str, object]] = []
    for candidate_id, reason in value.items():
        if not isinstance(candidate_id, str) or not isinstance(reason, str) or reason not in ALLOWED_SKIP_REASONS:
            errors.append(error("invalid_skip_reason", "Skip reasons must be one of the permitted runtime reasons.", id=candidate_id, reason=reason))
        else:
            skip_map[candidate_id] = reason
    return skip_map, errors


def draw(candidate_dir: Path, seed: str | None, skip_file: Path | None) -> dict[str, object]:
    pools, report = validate(candidate_dir)
    if report["errors"]:
        return report
    skip_map, skip_errors = load_skip_map(skip_file)
    if skip_errors:
        return {"valid": False, "errors": skip_errors, "checksums": report["checksums"]}
    actual_seed = seed if seed is not None else secrets.token_hex(16)
    selected: dict[str, dict[str, Any]] = {}
    ordered_ids: dict[str, list[str]] = {}
    skipped: list[dict[str, str]] = []
    for role in ROLES:
        ordered = ordered_candidates(pools[role], actual_seed, role)
        ordered_ids[role] = [item["id"] for item in ordered]
        for item in ordered:
            candidate_id = item["id"]
            if candidate_id in skip_map:
                skipped.append({"id": candidate_id, "reason": skip_map[candidate_id]})
                continue
            if item["availability"] == "ok":
                selected[role] = item
                break
        else:
            return {
                "valid": False,
                "errors": [error("no_drawable_candidate", "No available candidate remains after skips.", role=role)],
                "checksums": report["checksums"],
                "seed": actual_seed,
                "ordered_ids": ordered_ids,
                "skipped": skipped,
            }
    return {
        "valid": True,
        "errors": [],
        "checksums": report["checksums"],
        "seed": actual_seed,
        "ordered_ids": ordered_ids,
        "selected": selected,
        "skipped": skipped,
    }


def emit(payload: dict[str, object], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        output.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-pool", "validate", "draw"):
        command = subparsers.add_parser(name)
        command.add_argument("--candidate-dir", required=True, type=Path)
        command.add_argument("--output", type=Path)
        if name == "validate-pool":
            command.add_argument("--role", required=True, choices=sorted(ROLES))
        if name == "draw":
            command.add_argument("--seed")
            command.add_argument("--skip-file", type=Path)
    args = parser.parse_args(argv)

    if args.command == "draw":
        payload = draw(args.candidate_dir, args.seed, args.skip_file)
    else:
        roles = {args.role} if args.command == "validate-pool" else None
        _, payload = validate(args.candidate_dir, roles)
    emit(payload, args.output)
    return 0 if payload["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
