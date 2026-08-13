# Interactive UI reference universe

This directory freezes the candidate universe for a reproducible three-reference
interactive UI study. Selection is random and deterministic from a recorded seed;
subjective analysis begins only after the draw.

## Reference roles

Each run draws one candidate for each role. The roles have separate ownership so the
result does not become an arbitrary collage.

- `canvas_ui` is the Canvas/UI anchor. It owns the dominant working surface,
  information hierarchy, spatial organization, and the main objects or regions a
  user can manipulate.
- `motion_3d` is the Motion/3D mechanic. It owns behavior over time, object movement
  and transformation, depth, transition grammar, and direct-manipulation feedback.
- `color_art` is the Color/art direction. It owns palette, material, texture,
  lighting, atmosphere, image or illustration language, and emotional register.

The art reference may restyle the anchor but may not replace its canvas. The motion
reference may transform canvas objects but may not introduce an unrelated second
workspace.

## Candidate manifests

`candidates/` contains one JSON array per role. Every candidate is an object with
these required string fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable local candidate ID |
| `role` | `canvas_ui`, `motion_3d`, or `color_art` |
| `title` | Source project title |
| `creator` | Creator or studio credit |
| `canonical_url` | Canonical public project URL |
| `platform` | Source platform |
| `category` | Source category |
| `media_type` | `still`, `sequence`, `animation`, `video`, or `interactive_page` |
| `captured_at` | Capture date |
| `availability` | `ok` or a permitted skip reason |

Pre-draw records must not contain `aesthetic_score`, `usefulness_score`,
`model_preference`, or `vision_notes`. Selection metadata must not encode taste,
predicted usefulness, or a detailed visual judgment under another field.

## Pool balance and source quotas

The frozen target is 30 candidates per role. A pool needs at least 24 candidates
whose `availability` is `ok` before a draw is allowed.

- A platform may contribute at most 10 candidates to one pool.
- A creator or studio may contribute at most two candidates to one pool.
- Each pool must cover at least four categories.
- A canonical project URL may appear in only one pool.
- Near-duplicate reposts count as one project.

Sources are references, not assets for reuse. Preserve the creator credit and
canonical link. Transform principles instead of copying an exact composition,
asset, brand, or proprietary interface. Do not download, rehost, or commit source
images, video, audio, or other media; this research tree stores metadata and links
only.

## Permitted skips

A drawn candidate may be skipped only when:

- the page or required media is inaccessible (`inaccessible`);
- too little visual material loads for observation (`insufficient_media`);
- it duplicates another selected reference (`duplicate`); or
- the collected content does not match its declared role (`wrong_role`).

Difficulty, strangeness, weak usefulness, and personal taste are not permitted skip
reasons. A skip advances to the next candidate in the existing seeded order; it does
not trigger a new draw.

## Validate and draw

Run validation from the repository root:

```bash
.venv/bin/python scripts/interactive_ui_picker.py validate \
  --candidate-dir research/interactive-ui/candidates \
  --output research/interactive-ui/validation.json
```

Generate the run seed only after the manifests and `validation.json` are committed,
then draw in the same shell session:

```bash
PICKER_SEED="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')"
PICKER_RUN="research/interactive-ui/runs/20260812-${PICKER_SEED:0:8}"
mkdir -p "$PICKER_RUN"
.venv/bin/python scripts/interactive_ui_picker.py draw \
  --candidate-dir research/interactive-ui/candidates \
  --seed "$PICKER_SEED" \
  --output "$PICKER_RUN/selection.json"
```

The draw output records the seed, deterministic candidate order, selected records,
skips, and the three manifest checksums.

## Freeze rule

The SHA-256 checksums in `validation.json` identify this exact candidate universe.
Changing any frozen manifest invalidates its checksum and every selection tied to
that checksum. Do not overwrite or continue an existing run after such a change.
Validate the revised manifests, commit the new `validation.json`, generate a new
seed, and create a new run directory.
