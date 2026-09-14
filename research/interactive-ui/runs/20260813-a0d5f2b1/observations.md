## canvas_ui: Bronitex

- Creator: [Red Collar](https://redcollar.co/)
- Source: [Awwwards project page](https://www.awwwards.com/sites/bronitex)
- Evidence inspected: Awwwards Desktop hero still; sampled frames from the named Main Page, Catalog, and Materials clips; and the [Red Collar case study](https://redcollar.co/work/bronitex)

### Direct observations

- Composition and hierarchy: The hero is a full-bleed macro view of overlapping glove fingers on strong diagonals. A small top navigation sits above an oversized white headline. Later scenes switch between a black surround with one rounded white presentation panel, a spacious three-column catalog grid, a split product-detail layout, and a black feature stage centered on one glove.
- Palette, material, and lighting: Black and white carry most of the interface. Product color supplies the accents: yellow, green, blue, and orange in the hero; green and black in the detail and feature scenes. Close textile grain, knitted fibers, rubber coatings, and soft model shadows make the gloves feel physical.
- Visible objects or regions: The recurring regions are the glove model, sparse navigation, large display copy, material labels, catalog cards, product controls, a green call-to-action, and feature annotations connected to points on the product.
- Motion actually shown: The supplied clips show the hero giving way to an isolated rotating glove; a catalog sequence moving from product rows into a product detail; and a white glove becoming a green-and-yellow coated glove as the background turns black and feature labels appear around it. The clips show these visual changes, but do not establish which input triggers each change.

### Inferred interaction potential

- Mouse actions: Scroll through the staged product narrative, point at catalog cards, and operate the visible size, quantity, color, and cart controls. Red Collar describes scroll-linked scenes and hover-based photo changes, but the original live host was unavailable, so those inputs were not exercised here.
- Keyboard actions: Tab through navigation, cards, and form controls; use standard keys on the visible selectors and buttons. Keyboard behavior was not demonstrated by the inspected media.
- Visible response and state change: A selected catalog item could expand into the split detail layout, while a material or feature selection could move the object onto the dark inspection stage and reveal its annotations. This is a transfer inference from the visible states, not a verified Bronitex input map.

### Transferable principles

1. Let one oversized object dominate the canvas while navigation and controls remain quiet at its edges.
2. Use a white structured workspace and a black inspection stage as two clearly different information modes.
3. Turn material changes into legible state changes by pairing an object transformation with short, spatially attached labels.

### Unknowns

- `bronitex.redcollar.co` returned HTTP 502 during this pass. The Awwwards sequence and Red Collar case study provided enough direct visual evidence, but exact scroll thresholds, hover states, focus behavior, and current live performance remain unverified.

## motion_3d: Ragdoll Physics Engine.

- Creator: [FRADAR](https://codepen.io/FRADAR)
- Source: [CodePen pen](https://codepen.io/FRADAR/pen/MWpJzmQ)
- Evidence inspected: Actual [full preview](https://codepen.io/FRADAR/full/MWpJzmQ), embedded pen source, and five captured runtime states: empty field, falling, collision/settling, left-button drag, and post-release motion

### Direct observations

- Composition and hierarchy: A borderless white canvas fills the viewport. A solid blue floor band anchors the bottom edge. Two articulated figures enter from above, then become the only focal objects on the otherwise empty field.
- Palette, material, and lighting: Flat coral, cyan, lime, dark red, and charcoal parts use thin dark outlines. The blue ground is the only large color field. There is no modeled lighting, shading, texture, or depth cue beyond overlap and joint motion.
- Visible objects or regions: Each figure has a circular head, a composite torso and upper legs, separate rectangular arm and lower-leg segments, and visible constraint lines at the joints. Static floor and side bodies bound the simulation.
- Motion actually shown: Two figures spawn above the canvas, fall under gravity, flex at their constraints, collide with the floor and each other, and settle into different poses. In the live run, dragging one head lifted the connected body while its limbs lagged and swung; releasing it resumed gravity-driven motion.

### Inferred interaction potential

- Mouse actions: **Directly verified from source and runtime:** press the primary mouse button on a body, drag it across the canvas, and release it. `Mouse.create(canvas)` feeds `MouseConstraint`, and the dependency selects a body only while `mouse.button === 0`. No separate click, hover, wheel, or context-menu response is configured by the pen.
- Keyboard actions: **Directly verified absence:** the pen contains no `keydown`, `keyup`, or other keyboard listener, so keyboard input produces no authored response.
- Visible response and state change: **Directly verified:** a held body follows the pointer through a soft constraint, connected parts stretch and rotate, and release changes the figure from held to freely simulated. No additional response is inferred.

### Transferable principles

1. Model a complex object as independently moving regions joined by constraints rather than as one rigid card.
2. Preserve momentum after release so direct manipulation leaves a visible, temporary consequence.
3. Use collisions, settling, and bounded space to make state readable without adding explanatory chrome.

### Unknowns

- Spawn positions use `Math.random()`, so exact trajectories and resting poses vary by run. The source fixes the part geometry and constraint stiffness, but does not define a deterministic replay or an authored keyboard alternative.

## color_art: The Child's Bath

- Creator: Mary Cassatt
- Source: [Art Institute of Chicago object page](https://www.artic.edu/artworks/111442/the-childs-bath)
- Evidence inspected: [Official object API](https://api.artic.edu/api/v1/artworks/111442), [official IIIF manifest](https://api.artic.edu/api/v1/artworks/111442/manifest.json), and a full public-domain image plus close views of the faces and the hand/foot/basin area from the [AIC-attributed CC0 reproduction](https://commons.wikimedia.org/wiki/File:Mary_Cassatt_-_The_Child%27s_Bath_-_1910.2_-_Art_Institute_of_Chicago.jpg)

### Direct observations

- Composition and hierarchy: The vertical painting compresses the adult and child into one interlocking central mass viewed from above. Their adjacent heads begin a downward path through the encircling arm, the child's legs, the adult's hand, and the oval basin. A large cropped pitcher at lower right and diagonal floor pattern keep the lower half active.
- Palette, material, and lighting: Muted olive, sage, blue, lavender, and white dominate, with warm pink skin and terracotta-red floor accents. Visible brushwork and repeated stripes and floral marks create a matte, patterned surface. Light is diffuse; color and overlap separate forms more than cast shadow does.
- Visible objects or regions: The key regions are the touching faces, striped garment, the adult's supporting hand at the child's waist, the hand holding a foot in water, the oval basin, the cropped pitcher, the green floral background, and the patterned floor.
- Motion actually shown: None. The source is a single oil painting, and no motion sequence was inspected or implied as observed.

### Inferred interaction potential

- Mouse actions: A future canvas could let the pointer inspect, select, or pull apart related color and material regions. This interaction is not present in the painting.
- Keyboard actions: A future study could move focus among the same regions and open their details with standard activation keys. This interaction is not present in the painting.
- Visible response and state change: Selection could preserve the central chain from faces to hands to basin while increasing local contrast or exposing texture detail. This is an interface inference, not motion shown by the artwork.

### Transferable principles

1. Build hierarchy as a continuous visual path through adjacent forms rather than as isolated panels.
2. Combine a restrained cool field with small warm accents to direct attention without hard-edged highlighting.
3. Use repeated pattern and visible surface texture to bind separate regions into one intimate material world.

### Unknowns

- The AIC API confirms the work is public domain and identifies it as an 1893 oil on canvas, but the museum's IIIF image host presented a Cloudflare security check during this pass. The official API and manifest remained accessible; visual inspection used the AIC-credited CC0 reproduction linked above.
