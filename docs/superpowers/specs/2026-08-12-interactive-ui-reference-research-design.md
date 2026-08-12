# Interactive UI Reference Research — Design Spec

Status: approved in conversation; awaiting review of this written spec

Date: 2026-08-12

Owner: nathan

## 1. Goal

Run a repeatable visual-research process that produces one coherent interactive UI
direction from three independently selected creative references.

The result is an **interactive UI study**, not necessarily a complete product. Its
value should come primarily from the canvas, visual language, and interaction loop.
A lightweight personal-productivity premise may give the study a reason to exist,
but usefulness and production readiness are secondary.

The first end-to-end run stops after it has:

1. built three sufficiently varied candidate pools;
2. frozen and hashed those pools;
3. selected one reference from each pool with a recorded random seed; and
4. produced a short observation card for the resulting three-reference kit.

Later work may use that kit to create five UI concepts and select one concept for a
prototype.

## 2. Target artifact

A valid eventual artifact has:

- one dominant canvas or working surface;
- an interaction loop: user action, visible feedback, state change, and another
  meaningful action;
- input limited to mouse and keyboard;
- a strong and coherent visual identity;
- enough state to explore the interaction, using generated or fixture data when
  useful.

The artifact does **not** require:

- real user or business data;
- backend services, accounts, persistence, or synchronization;
- a complete product flow;
- commercial usefulness;
- production feasibility.

Two-dimensional and three-dimensional screen-based interfaces are both eligible.
Camera, microphone, voice, device sensors, and specialist hardware are out of scope.

## 3. Research principle: canvas first, product second

The process does not begin by mining a practical pain point and optimizing a familiar
application around it. It begins with a visual or spatial system that may support an
interesting interaction. A small personal-use premise is added only when it helps the
interaction become understandable.

The model must not choose references by asking which project looks most attractive.
That would reintroduce model-distribution bias at the selection step. Randomness owns
selection; visual analysis owns description and transfer.

Platform curation still introduces its own bias. The pools therefore balance sources,
categories, and creators instead of sampling only the first page of one UI gallery.

## 4. The three reference roles

Each run selects exactly one reference for each role.

### 4.1 Canvas/UI anchor

Owns:

- the dominant surface;
- information hierarchy;
- spatial organization;
- the main objects or regions a user can manipulate.

Typical source categories include UI/UX, product design, game UI, data visualization,
editor interfaces, maps, dashboards, and experimental web interfaces.

Eligibility requires a readable working surface that can support multiple visible
states. The project need not depict a functioning application.

### 4.2 Motion/3D mechanic

Owns:

- what changes over time;
- how objects move, transform, connect, separate, stack, or reveal depth;
- transition grammar and direct-manipulation feedback.

Typical source categories include motion design, 3D art, kinetic typography,
generative art, game mechanics, creative coding, and interactive experiments.

Eligibility requires at least one transferable behavior that can be driven by mouse
or keyboard. A rendered video is acceptable as a reference even though the source
itself is not interactive.

### 4.3 Color/art direction

Owns:

- palette and color relationships;
- material, texture, lighting, and atmosphere;
- illustration or image language;
- the emotional register of the final study.

Typical source categories include illustration, fine art, graphic and editorial
design, photography, painting, posters, branding, and digital artwork.

Eligibility requires a distinctive visual system. It does not require any UI or
implied interaction.

## 5. Candidate pool contract

The first run targets **30 candidates per role**, for 90 references total and up to
27,000 possible three-reference kits.

Each candidate record contains only selection metadata before the draw:

- stable local ID;
- assigned role;
- title and creator or studio;
- canonical source URL;
- source platform;
- source category;
- media type: still, sequence, animation, video, or interactive page;
- date captured;
- availability status.

No aesthetic score, predicted usefulness, model preference, or detailed vision note
may be stored before selection.

### 5.1 Balance rules

- No platform contributes more than 10 candidates to one pool.
- No creator or studio contributes more than two candidates to one pool.
- The same canonical project URL cannot appear in more than one pool.
- Near-duplicate reposts count as one project.
- A pool must cover at least four source categories.
- A pool must contain at least 24 valid candidates after access checks. The target
  remains 30; 24 is the hard minimum for allowing a draw.

### 5.2 Source acquisition modes

Not every source should be crawled the same way.

- **Structured collection:** use an official public API or documented dataset when
  available, such as Are.na or open museum collections.
- **Search-assisted collection:** use web and image search to discover public project
  pages on platforms whose public API does not provide discovery feeds.
- **Visual review:** inspect the project page, long-form images, GIFs, or embedded
  videos and retain the canonical link plus minimal metadata.

Behance and Dribbble belong primarily to search-assisted collection and visual review.
The process must not depend on undocumented bulk-scraping endpoints.

Candidate discovery can use UI/UX, Motion, 3D Art, Game Design, Illustration, Fine
Arts, Graphic Design, and adjacent categories. It must not be restricted to technology
productivity products.

## 6. Freeze and random-selection protocol

Before selection:

1. normalize and deduplicate all candidate records;
2. run the eligibility and balance checks;
3. sort records by stable local ID;
4. serialize each pool to a canonical manifest;
5. record a checksum for each manifest.

The picker then accepts or generates one seed, creates a deterministic shuffled order
for each pool, and selects the first eligible item from each order.

The run record contains:

- seed;
- timestamp;
- three pool checksums;
- shuffled position and selected ID for every role;
- any skipped candidate and its permitted skip reason.

Permitted skip reasons are limited to:

- the page or required media is no longer accessible;
- insufficient visual material loads for observation;
- the item is a duplicate of another selected reference;
- the collected content does not match its declared role.

Difficulty, strangeness, weak usefulness, or personal taste are not permitted reasons.
Fallback proceeds to the next item in the already shuffled order; it never performs a
new draw.

## 7. Post-draw visual observation

Only after the three references have been selected does the vision pass begin.

For each reference, capture enough of the project to understand it: hero image, key
screens or compositions, and motion frames or embedded media when present. A long
Behance case study should not be judged from its thumbnail alone.

The observation card records:

- visible composition and hierarchy;
- palette, contrast, texture, material, and lighting;
- objects or regions that could become interactive;
- actual motion shown separately from motion inferred by the reviewer;
- possible mouse and keyboard actions;
- visible response and state transformation;
- one to three transferable principles;
- ambiguities and inaccessible evidence;
- source URL and creator credit.

Observations must distinguish what is directly visible from what the reviewer infers.
The pass describes the selected reference; it does not retroactively score whether a
different candidate should have been chosen.

## 8. Combining the selected kit

The three roles have explicit ownership to prevent an incoherent collage:

- the anchor supplies the canvas and hierarchy;
- the mechanic supplies behavior and state transitions;
- the art reference supplies palette, material, and atmosphere.

The synthesis must transform rather than copy the sources. It may reuse an abstract
principle such as layering, elastic tension, orbit, erosion, folding, or color mixing.
It must not reproduce another creator's exact composition, assets, branding, or
proprietary interface.

Any conflict is resolved by role ownership. For example, the art reference may recolor
the anchor, but it may not replace the anchor's canvas with a poster layout. The motion
reference may transform canvas objects, but it may not add an unrelated second
workspace.

## 9. Five-concept divergence and convergence

After the first-run checkpoint, a later synthesis pass creates exactly five concepts:

1. **Anchor-led:** closest to the selected UI/canvas structure.
2. **Mechanic-led:** maximizes manipulation and state change.
3. **Art-led:** turns the selected visual world into an interface material.
4. **Spatial experiment:** takes the strongest nonstandard or three-dimensional path.
5. **Integrated:** combines the strongest compatible choices from the first four.

Each concept states:

- its one-sentence premise;
- canvas model;
- primary objects;
- mouse and keyboard actions;
- interaction loop;
- visual and motion response;
- which principle came from each reference;
- what fake data or fixture state it needs.

One concept is selected with these weights:

- 45% interaction depth;
- 25% visual attraction and coherence;
- 20% originality created by the collision;
- 5% legibility of feedback;
- 5% minimal use premise.

Product completeness and real-data readiness receive no score. Technical feasibility is
only a hard boundary: the behavior must be representable in a frontend with mouse and
keyboard.

## 10. First end-to-end run deliverables

The currently approved execution stops with one selected three-reference kit. It must
produce:

1. three frozen candidate manifests with at least 24 and a target of 30 valid items
   each;
2. a validation report for pool size, source/category/creator balance, canonical URL
   uniqueness, and checksums;
3. the picker script;
4. a run record containing the seed and exact selections;
5. one observation card per selected reference;
6. a short kit summary explaining what each reference owns and where the combination
   may create tension.

No UI implementation, product backend, or five-concept synthesis is required for this
checkpoint.

## 11. Verification

Before reporting the first run complete:

- rerun the picker with the recorded seed and confirm the same three IDs;
- change the seed in a test run and confirm selection can change without editing the
  manifests;
- confirm every selected URL and required visual media is accessible;
- verify candidate counts and balance rules mechanically;
- confirm no selected item was chosen or replaced using an aesthetic score;
- manually check that the three observation cards separate direct observation from
  inference;
- confirm creator credits and canonical links remain attached to every observation.
