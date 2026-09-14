/** How a tool call's output is drawn.
 *
 * A transcript carries one shape of output per tool, and a wall of text is the
 * wrong reading of most of them. So the box does not draw the output itself:
 * it asks this registry which component draws this line, and that component
 * decides what a reader should see first.
 *
 * Adding one is the whole point, and it takes no change here:
 *
 *     import { toolRenderers } from ".../tool_renderers";
 *     toolRenderers.add("diff", { match: (line) => line.tool_name === "Edit",
 *                                 Component: MyDiff }, { sequence: 20 });
 *
 * `match(line)` gets the row as the box holds it — role, tool_name, tool_input,
 * tool_output, tool_images — and the first entry that says yes wins, in
 * sequence order. A renderer is a component taking `{ line, open }`, where
 * `open` is the reader having expanded the call. Anything drawn outside that
 * flag shows in the collapsed state, which is where a preview belongs.
 */
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";

export const toolRenderers = registry.category("flight_deck.tool_renderers");

// A URL in the output text, which the browser loads itself. Bounded on both
// sides so a URL inside quotes or brackets ends where it should.
const IMAGE_URL = /https?:\/\/[^\s"'<>)\]]+?\.(?:png|jpe?g|gif|webp)(?:\?[^\s"'<>)\]]*)?/gi;
// Enough for a screenshot set; a cap so a pathological output cannot paint
// hundreds of requests.
const MAX_SHOTS = 12;

/** The images a line carries, whatever they came from.
 *
 * Two sources, and the second is the common one: a URL written in the output
 * text, and the image blocks the call actually returned, which stay in the
 * transcript file and are served by their position in it.
 */
export function shotsOf(line) {
    const shots = [];
    let blocks = [];
    try {
        blocks = JSON.parse(line.tool_images || "[]");
    } catch {
        blocks = [];
    }
    for (const block of blocks) {
        shots.push({
            url: `/flight_deck/tool_image/${line.id}/${block.index}`,
            label: block.media_type || "image",
        });
    }
    for (const url of String(line.tool_output || "").match(IMAGE_URL) || []) {
        if (!shots.some((shot) => shot.url === url)) {
            shots.push({ url, label: url });
        }
    }
    return shots.slice(0, MAX_SHOTS);
}

/** The output as it was recorded. What every tool gets until one earns more. */
export class ToolRaw extends Component {
    static template = "flight_deck.ToolRaw";
    static props = { line: Object, open: { type: Boolean, optional: true } };
}

/** A call that produced images: the images, then the text behind Expand. */
export class ToolImages extends Component {
    static template = "flight_deck.ToolImages";
    static props = { line: Object, open: { type: Boolean, optional: true } };

    setup() {
        // A URL written in an output is whatever it was at the time: the host
        // may be gone. One that does not load leaves nothing behind.
        this.state = useState({ failed: {} });
    }

    get shots() {
        return shotsOf(this.props.line).filter((shot) => !this.state.failed[shot.url]);
    }

    onShotError(url) {
        this.state.failed[url] = true;
    }
}

toolRenderers.add(
    "images",
    { match: (line) => shotsOf(line).length > 0, Component: ToolImages },
    { sequence: 10 }
);

/** Which component draws this call. Falls back to the raw output. */
export function rendererFor(line) {
    for (const entry of toolRenderers.getAll()) {
        try {
            if (entry.match(line)) {
                return entry.Component;
            }
        } catch {
            // A renderer that cannot read a line does not get to break the
            // transcript: the next one, or the raw output, draws it.
        }
    }
    return ToolRaw;
}
