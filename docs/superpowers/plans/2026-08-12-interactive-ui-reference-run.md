# Interactive UI Reference Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, and freeze three 30-item visual-reference pools, select one role-based three-reference kit reproducibly, and document what can be transferred from each selected reference into an interactive UI study.

**Architecture:** A Python standard-library CLI validates canonical JSON manifests and orders candidates with a SHA-256 key derived from a seed, role, and stable candidate ID. Candidate research is split into three independent manifests so it can run in parallel; after all manifests pass balance rules, one immutable selection record drives a visual observation pass that stores prose and source links, not copyrighted source media.

**Tech Stack:** Python 3.12 standard library, pytest, JSON manifests, Markdown research artifacts, web/image search, Chrome headless for temporary visual inspection.

## Global Constraints

- The result is an interactive UI study; real data, backend services, accounts, persistence, and complete product flows are not required.
- Eventual interaction input is limited to mouse and keyboard; two-dimensional and three-dimensional screen-based interfaces are eligible.
- Each role targets 30 valid candidates and must contain at least 24 before drawing.
- No platform may contribute more than 10 candidates to one pool.
- No creator or studio may contribute more than two candidates to one pool.
- Each pool must cover at least four source categories.
- A canonical project URL may appear in only one pool.
- No aesthetic score, predicted usefulness, model preference, or detailed vision note may exist before the draw.
- Behance and Dribbble use search-assisted discovery and visual review, not undocumented bulk endpoints.
- Selection is seed-driven; difficulty, strangeness, usefulness, and personal taste are not skip reasons.
- The selected kit contains exactly one `canvas_ui`, one `motion_3d`, and one `color_art` reference.
- Do not store downloaded reference images in Git. Use temporary screenshots for review and preserve canonical URLs and creator credits in the written artifacts.
- Preserve all unrelated existing FlightDeck changes.

## File Map

- Create `scripts/interactive_ui_picker.py`: manifest validation, canonical checksums, deterministic ordering, constrained skips, and CLI output.
- Create `backend/tests/test_interactive_ui_picker.py`: black-box CLI tests for validation, reproducibility, checksums, and skip rules.
- Create `research/interactive-ui/README.md`: manifest schema, source policy, and exact reproduction commands.
- Create `research/interactive-ui/candidates/canvas-ui.json`: 30 canvas/UI references.
- Create `research/interactive-ui/candidates/motion-3d.json`: 30 motion/3D references.
- Create `research/interactive-ui/candidates/color-art.json`: 30 color/art-direction references.
- Create `research/interactive-ui/validation.json`: frozen counts, balance metrics, and manifest checksums.
- Create `research/interactive-ui/runs/20260812-<seed-prefix>/selection.json`: generated seed, deterministic order, selected IDs, and pool checksums. `<seed-prefix>` is computed from the first eight hexadecimal characters of the generated seed.
- Create `research/interactive-ui/runs/20260812-<seed-prefix>/observations.md`: three evidence-separated visual observation cards.
- Create `research/interactive-ui/runs/20260812-<seed-prefix>/kit-summary.md`: role ownership, transferable principles, and tensions in the selected kit.

---

### Task 1: Deterministic picker and validation CLI

**Files:**
- Create: `scripts/interactive_ui_picker.py`
- Create: `backend/tests/test_interactive_ui_picker.py`

**Interfaces:**
- Consumes: three JSON arrays named `canvas-ui.json`, `motion-3d.json`, and `color-art.json`.
- Produces: `validate-pool`, `validate`, and `draw` CLI commands; JSON reports on stdout or at `--output`.
- Candidate fields: `id`, `role`, `title`, `creator`, `canonical_url`, `platform`, `category`, `media_type`, `captured_at`, `availability`.
- Roles: `canvas_ui`, `motion_3d`, `color_art`.
- Availability values: `ok`, `inaccessible`, `insufficient_media`, `duplicate`, `wrong_role`.
- Permitted runtime skip reasons: `inaccessible`, `insufficient_media`, `duplicate`, `wrong_role`.

- [ ] **Step 1: Write black-box tests for a valid pool and reproducible draw**

Create fixtures in `tmp_path` with 24 records per role, four platforms with six records each, unique creators, four categories, and unique URLs. Invoke the CLI through `subprocess.run`:

```python
ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "interactive_ui_picker.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


ROLE_FILES = {
    "canvas_ui": "canvas-ui.json",
    "motion_3d": "motion-3d.json",
    "color_art": "color-art.json",
}


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
```

- [ ] **Step 2: Run the test and confirm it fails because the CLI is absent**

Run:

```bash
.venv/bin/pytest backend/tests/test_interactive_ui_picker.py::test_draw_is_reproducible -v
```

Expected: FAIL because `scripts/interactive_ui_picker.py` does not exist.

- [ ] **Step 3: Write validation failure tests**

Cover these exact failures:

```python
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
```

- [ ] **Step 4: Write constrained-skip tests**

```python
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
```

- [ ] **Step 5: Implement the CLI with standard-library-only deterministic ordering**

Use canonical JSON serialization for checksums and hash-sort candidates instead of relying on Python's shuffle implementation:

```python
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
    "aesthetic_score", "usefulness_score", "model_preference", "vision_notes"
}
ALLOWED_SKIP_REASONS = {
    "inaccessible", "insufficient_media", "duplicate", "wrong_role"
}


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def checksum(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def order_key(seed: str, role: str, candidate_id: str) -> str:
    material = f"{seed}\0{role}\0{candidate_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def ordered_candidates(records: list[dict], seed: str, role: str) -> list[dict]:
    return sorted(records, key=lambda item: order_key(seed, role, item["id"]))
```

Validation returns every error in one report instead of stopping on the first. `draw` calls full validation first, generates `secrets.token_hex(16)` when `--seed` is absent, rejects unknown skip reasons, records the full ordered ID list per role, and selects the first `availability == "ok"` item not present in the skip map.

- [ ] **Step 6: Run focused and full picker tests**

Run:

```bash
.venv/bin/pytest backend/tests/test_interactive_ui_picker.py -v
```

Expected: all picker tests PASS.

- [ ] **Step 7: Commit the picker**

```bash
git add scripts/interactive_ui_picker.py backend/tests/test_interactive_ui_picker.py
git commit -m "feat(research): add deterministic reference picker"
```

### Task 2: Canvas/UI anchor pool

**Files:**
- Create: `research/interactive-ui/candidates/canvas-ui.json`

**Interfaces:**
- Consumes: public project pages found through search-assisted discovery and visual review.
- Produces: exactly 30 records with `role: "canvas_ui"`, ready for `validate-pool`.

- [ ] **Step 1: Collect a balanced 30-item source set**

Use these source quotas:

- 10 Behance curated UI/UX or Product Design projects;
- 5 Dribbble project or shot pages found through public search;
- 10 Awwwards project pages;
- 5 Figma Community files or public prototype pages.

Cover at least four categories among `ui_ux`, `experimental_web`, `data_visualization`, `map_spatial`, `game_ui`, and `editor_canvas`. Do not rank candidates. Confirm only that each page exposes a readable dominant surface with multiple potential states.

- [ ] **Step 2: Record canonical metadata**

Each entry uses this exact shape:

```json
{
  "id": "canvas-001",
  "role": "canvas_ui",
  "title": "Project title",
  "creator": "Creator or studio",
  "canonical_url": "https://canonical.example/project",
  "platform": "Behance",
  "category": "map_spatial",
  "media_type": "sequence",
  "captured_at": "2026-08-12",
  "availability": "ok"
}
```

Strip tracking query parameters. IDs run from `canvas-001` through `canvas-030` after the list is frozen; IDs must not encode quality or source rank.

- [ ] **Step 3: Validate the pool independently**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py validate-pool \
  --path research/interactive-ui/candidates/canvas-ui.json \
  --role canvas_ui
```

Expected: exit 0, count 30, no errors, no platform above 10, no creator above 2, at least four categories.

- [ ] **Step 4: Commit the canvas pool**

```bash
git add research/interactive-ui/candidates/canvas-ui.json
git commit -m "docs(research): collect canvas references"
```

### Task 3: Motion/3D mechanic pool

**Files:**
- Create: `research/interactive-ui/candidates/motion-3d.json`

**Interfaces:**
- Consumes: public motion, 3D, game-mechanic, generative, and creative-code references.
- Produces: exactly 30 records with `role: "motion_3d"`, ready for `validate-pool`.

- [ ] **Step 1: Collect a balanced 30-item source set**

Use these source quotas:

- 10 Behance Motion, 3D Art, or Game Design projects;
- 10 CodePen public pens;
- 5 Awwwards project pages with meaningful motion or spatial behavior;
- 5 Observable public notebooks or experiments.

Cover at least four categories among `motion_design`, `spatial_3d`, `kinetic_type`, `generative_system`, `game_mechanic`, and `physics_direct_manipulation`. A video-only source is eligible if it shows a behavior that mouse or keyboard can drive in the eventual study.

- [ ] **Step 2: Record canonical metadata**

Use IDs `motion-001` through `motion-030`. Every entry has this exact shape, with the actual values from the source. `media_type` must distinguish `animation`, `video`, and `interactive_page` when known:

```json
{
  "id": "motion-001",
  "role": "motion_3d",
  "title": "Project title",
  "creator": "Creator or studio",
  "canonical_url": "https://canonical.example/project",
  "platform": "CodePen",
  "category": "physics_direct_manipulation",
  "media_type": "interactive_page",
  "captured_at": "2026-08-12",
  "availability": "ok"
}
```

- [ ] **Step 3: Validate the pool independently**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py validate-pool \
  --path research/interactive-ui/candidates/motion-3d.json \
  --role motion_3d
```

Expected: exit 0, count 30, no errors, no platform above 10, no creator above 2, at least four categories.

- [ ] **Step 4: Commit the mechanic pool**

```bash
git add research/interactive-ui/candidates/motion-3d.json
git commit -m "docs(research): collect motion references"
```

### Task 4: Color/art-direction pool

**Files:**
- Create: `research/interactive-ui/candidates/color-art.json`

**Interfaces:**
- Consumes: public design projects and open-access artwork records.
- Produces: exactly 30 records with `role: "color_art"`, ready for `validate-pool`.

- [ ] **Step 1: Collect a balanced 30-item source set**

Use these source quotas:

- 10 Behance Illustration, Fine Arts, Graphic Design, or Photography projects;
- 10 Art Institute of Chicago public artwork records;
- 5 Metropolitan Museum of Art public collection records;
- 5 Europeana public records.

Cover at least four categories among `illustration`, `painting`, `graphic_editorial`, `photography`, `digital_art`, `material_light`, and `color_system`. Prefer public-domain museum images for inspection, confirm the rights statement on the canonical record page, and do not commit the media.

- [ ] **Step 2: Record canonical metadata**

Use IDs `art-001` through `art-030`. Creator strings use the credited artist or `Unknown artist` when the collection record says the author is unknown; do not invent missing attribution. Every entry has this exact shape:

```json
{
  "id": "art-001",
  "role": "color_art",
  "title": "Artwork or project title",
  "creator": "Credited artist or Unknown artist",
  "canonical_url": "https://canonical.example/record",
  "platform": "Art Institute of Chicago",
  "category": "color_system",
  "media_type": "still",
  "captured_at": "2026-08-12",
  "availability": "ok"
}
```

- [ ] **Step 3: Validate the pool independently**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py validate-pool \
  --path research/interactive-ui/candidates/color-art.json \
  --role color_art
```

Expected: exit 0, count 30, no errors, no platform above 10, no creator above 2, at least four categories.

- [ ] **Step 4: Commit the art-direction pool**

```bash
git add research/interactive-ui/candidates/color-art.json
git commit -m "docs(research): collect art references"
```

### Task 5: Freeze and validate the complete candidate universe

**Files:**
- Create: `research/interactive-ui/README.md`
- Create: `research/interactive-ui/validation.json`

**Interfaces:**
- Consumes: the three candidate manifests and `scripts/interactive_ui_picker.py validate`.
- Produces: immutable manifest checksums and documented reproduction commands used by Task 6.

- [ ] **Step 1: Write the research README**

Document:

- the three role definitions and ownership boundaries;
- the candidate JSON schema and forbidden pre-draw fields;
- source quotas and copyright rule;
- permitted skip reasons;
- exact `validate` and `draw` commands;
- the rule that changing a frozen manifest invalidates its checksum and requires a new run.

- [ ] **Step 2: Generate the validation artifact**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py validate \
  --candidate-dir research/interactive-ui/candidates \
  --output research/interactive-ui/validation.json
```

Expected: exit 0; three counts of 30; zero errors; three SHA-256 checksums; cross-pool URL uniqueness true.

- [ ] **Step 3: Add an integration test for the committed manifests**

Append:

```python
def test_committed_manifests_pass_contract():
    result = run_cli(
        "validate",
        "--candidate-dir", str(ROOT / "research" / "interactive-ui" / "candidates"),
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(result.stdout)
    assert {role: item["count"] for role, item in payload["pools"].items()} == {
        "canvas_ui": 30,
        "motion_3d": 30,
        "color_art": 30,
    }
    assert payload["errors"] == []
```

- [ ] **Step 4: Run all picker and manifest tests**

Run:

```bash
.venv/bin/pytest backend/tests/test_interactive_ui_picker.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit the frozen candidate universe**

```bash
git add research/interactive-ui/README.md research/interactive-ui/validation.json \
  backend/tests/test_interactive_ui_picker.py
git commit -m "test(research): freeze reference universe"
```

### Task 6: Draw and reproduce one three-reference kit

**Files:**
- Create: `research/interactive-ui/runs/20260812-<seed-prefix>/selection.json`

**Interfaces:**
- Consumes: frozen manifest checksums from `validation.json`.
- Produces: one immutable selected record per role, full deterministic candidate order, and any constrained skip record.

- [ ] **Step 1: Generate the seed only after the manifests are committed**

Run:

```bash
PICKER_SEED="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')"
PICKER_RUN="research/interactive-ui/runs/20260812-${PICKER_SEED:0:8}"
mkdir -p "$PICKER_RUN"
```

Keep `PICKER_SEED` and `PICKER_RUN` in the same shell session for the next steps.

- [ ] **Step 2: Draw the kit**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py draw \
  --candidate-dir research/interactive-ui/candidates \
  --seed "$PICKER_SEED" \
  --output "$PICKER_RUN/selection.json"
```

Expected: exit 0; one selection for each role; selection checksums match `validation.json`; no skip unless a permitted access failure is recorded.

- [ ] **Step 3: Prove deterministic reproduction**

Run:

```bash
.venv/bin/python scripts/interactive_ui_picker.py draw \
  --candidate-dir research/interactive-ui/candidates \
  --seed "$PICKER_SEED" \
  --output "$PICKER_RUN/selection.reproduced.json"
cmp "$PICKER_RUN/selection.json" "$PICKER_RUN/selection.reproduced.json"
rm -- "$PICKER_RUN/selection.reproduced.json"
```

Expected: `cmp` exits 0. Remove only the generated reproduction file after the check; it is not a deliverable.

- [ ] **Step 4: Confirm a different seed can change the kit**

Run this bounded check; it tries up to 20 independent alternate seeds without changing the manifests or real run seed:

```bash
PICKER_ALT_FILE="$(mktemp)"
PICKER_REAL_SELECTION="$PICKER_RUN/selection.json" \
PICKER_ALT_FILE="$PICKER_ALT_FILE" \
.venv/bin/python - <<'PY'
import json
import os
import secrets
import subprocess
import sys

real_path = os.environ["PICKER_REAL_SELECTION"]
alt_path = os.environ["PICKER_ALT_FILE"]
real = json.load(open(real_path, encoding="utf-8"))["selected"]
for _ in range(20):
    seed = secrets.token_hex(16)
    result = subprocess.run(
        [sys.executable, "scripts/interactive_ui_picker.py", "draw",
         "--candidate-dir", "research/interactive-ui/candidates",
         "--seed", seed, "--output", alt_path],
        check=True,
    )
    alternate = json.load(open(alt_path, encoding="utf-8"))["selected"]
    if any(alternate[role]["id"] != real[role]["id"] for role in real):
        print(seed)
        break
else:
    raise SystemExit("20 alternate seeds produced the same kit")
PY
rm -- "$PICKER_ALT_FILE"
```

Expected: the script prints an alternate seed and exits 0.

- [ ] **Step 5: Commit the immutable selection record**

```bash
git add "$PICKER_RUN/selection.json"
git commit -m "docs(research): draw reference kit"
```

### Task 7: Observe the selected references and summarize the kit

**Files:**
- Create: `research/interactive-ui/runs/20260812-<seed-prefix>/observations.md`
- Create: `research/interactive-ui/runs/20260812-<seed-prefix>/kit-summary.md`

**Interfaces:**
- Consumes: only the three project URLs in the immutable `selection.json` plus enough temporary screenshots or video frames to inspect each source.
- Produces: three evidence-separated observation cards and one role-aware synthesis summary.

- [ ] **Step 1: Capture enough visual evidence for each selected source**

For long project pages, inspect the hero and at least two additional meaningful sections. For animation or video, inspect at least three distinct frames or the actual clip. Store screenshots only under a `mktemp -d` directory; do not add them to Git.

If a selected source now meets one of the four permitted skip conditions, create a temporary JSON skip map containing its selected ID and exact reason, rerun `draw` with the original seed and `--skip-file`, and overwrite `selection.json`. Repeat access inspection on the deterministic successor. The replacement selection must preserve the original seed and record the skipped ID and reason in `selection.json`.

- [ ] **Step 2: Write one observation card per role**

Use this exact structure for all three cards:

```markdown
## <role>: <title>

- Creator: <credited creator>
- Source: <canonical URL>
- Evidence inspected: <hero, named sections, frames, or interactive states>

### Direct observations

- Composition and hierarchy: ...
- Palette, material, and lighting: ...
- Visible objects or regions: ...
- Motion actually shown: ...

### Inferred interaction potential

- Mouse actions: ...
- Keyboard actions: ...
- Visible response and state change: ...

### Transferable principles

1. ...
2. ...
3. ...

### Unknowns

- ...
```

Do not claim motion was present when it was inferred from still frames. Do not score the source or compare it with candidates that were not selected.

- [ ] **Step 3: Write the kit summary**

State:

- what the anchor owns in the prospective canvas;
- what the mechanic owns in transitions and state;
- what the art reference owns in palette and material;
- two or three promising collisions;
- two or three tensions or incompatibilities;
- the minimum fake-data state needed to explore the kit later;
- why no reroll is required.

Do not generate the five concepts yet; that work is outside this checkpoint.

- [ ] **Step 4: Run final verification**

Run:

```bash
.venv/bin/pytest backend/tests/test_interactive_ui_picker.py -v
.venv/bin/python scripts/interactive_ui_picker.py validate \
  --candidate-dir research/interactive-ui/candidates
git diff --check HEAD
```

Then manually verify that all three selected URLs still open, all creator credits match the source, direct observation and inference are separate, and no source media is tracked by Git.

- [ ] **Step 5: Commit the observation artifacts**

```bash
git add research/interactive-ui/runs/20260812-*/observations.md \
  research/interactive-ui/runs/20260812-*/kit-summary.md
git commit -m "docs(research): analyze selected kit"
```
