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
 * tool_output, tool_images, tool_call_offset — and the first entry that says yes
 * wins, in
 * sequence order. A renderer is a component taking `{ line, open }`, where
 * `open` is the reader having expanded the call. Anything drawn outside that
 * flag shows in the collapsed state, which is where a preview belongs.
 */
import { Component, onWillUpdateProps, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { renderMarkdown } from "./markdown";

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

// The name a call wrote to, taken out of the input without parsing it: the
// input is stored capped, so on a call that wrote a file it is usually not
// valid JSON any more, while the name sits in its first few bytes.
const FILE_PATH = /"file_path"\s*:\s*"((?:[^"\\]|\\.)*)"/;
// What a written file is shown as, by the name it was written to. Same table as
// the server's, and the server is the one that decides: this only says whether
// asking is worth a request.
const PREVIEW_KINDS = {
    svg: "image",
    html: "frame",
    htm: "frame",
    md: "markdown",
    markdown: "markdown",
};
const MARKUP_EXT = ["svg", "html", "htm"];

/** What this call can be previewed as: `{ kind, name, url }`, or null.
 *
 * A row stored before the offset existed has none, and no preview: the input
 * column holds a few thousand characters of a file that may be far longer, and
 * half a drawing is worse than no drawing.
 */
export function previewOf(line) {
    // The call whose id the row carries is the first one it made, and it is the
    // one the server reads back. Every other tool that names a `file_path` —
    // Read, Edit, a published artifact — wrote no content to show.
    if (!line.tool_call_offset || String(line.tool_name || "").split(",")[0].trim() !== "Write") {
        return null;
    }
    const found = FILE_PATH.exec(line.tool_input || "");
    if (!found) {
        return null;
    }
    let path = found[1];
    try {
        path = JSON.parse(`"${path}"`);
    } catch {
        // A path the cap cut in half: the extension it ends on is not its own.
        return null;
    }
    const name = path.split("/").pop();
    const parts = name.toLowerCase().split(".").slice(1);
    let kind = PREVIEW_KINDS[parts[parts.length - 1]];
    if (kind === "markdown" && parts.slice(0, -1).some((p) => MARKUP_EXT.includes(p))) {
        kind = "frame";
    }
    return kind ? { kind, name, url: `/flight_deck/tool_content/${line.id}` } : null;
}

/** One preview at the size it was drawn for.
 *
 * Over the transcript rather than in a tab of its own: the drawing is being
 * read against the turn that produced it, and a tab loses that.
 */
export class ToolPreviewDialog extends Component {
    static template = "flight_deck.ToolPreviewDialog";
    static components = { Dialog };
    static props = {
        preview: Object,
        html: { optional: true },
        close: { type: Function, optional: true },
    };
}

/** A call that wrote something showable: the thing itself, not the JSON of it.
 *
 * Three ways to show one, and none of them lets the file become markup in this
 * page. A drawing goes in an `img`, where a browser runs nothing an SVG
 * carries and the drawing sizes itself. A page goes in a sandboxed frame.
 * Markdown is drawn here only because the renderer escapes everything before
 * it emits a single tag.
 */
export class ToolPreview extends Component {
    static template = "flight_deck.ToolPreview";
    static props = { line: Object, open: { type: Boolean, optional: true } };

    setup() {
        this.dialog = useService("dialog");
        this.state = useState({ html: null, failed: false });
        this.load(this.props.line);
        onWillUpdateProps((next) => {
            if (next.line.id !== this.props.line.id) {
                this.load(next.line);
            }
        });
    }

    get preview() {
        return previewOf(this.props.line);
    }

    async load(line) {
        const found = previewOf(line);
        this.state.html = null;
        this.state.failed = false;
        if (!found || found.kind !== "markdown") {
            return;
        }
        try {
            const answer = await fetch(found.url);
            if (!answer.ok) {
                throw new Error(answer.status);
            }
            this.state.html = renderMarkdown(await answer.text());
        } catch {
            // The file is only in the transcript, and a transcript that was
            // rewritten no longer has it. The call still reads as a call.
            this.state.failed = true;
        }
    }

    onFrameError() {
        this.state.failed = true;
    }

    onOpen() {
        this.dialog.add(ToolPreviewDialog, { preview: this.preview, html: this.state.html });
    }
}

toolRenderers.add(
    "preview",
    { match: (line) => previewOf(line) !== null, Component: ToolPreview },
    { sequence: 20 }
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
