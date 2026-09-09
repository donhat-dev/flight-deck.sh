/** Put the transcript box where the attachment preview would go.
 *
 * Odoo compiles a form arch through a registry keyed by a CSS selector matched
 * against the arch, so `<div class="o_fd_transcript"/>` in the arch is not a
 * div — it is this component's mount point. Same mechanism `mail` uses for
 * `div.o_attachment_preview`.
 */
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";
import { append, createElement, setAttributes } from "@web/core/utils/xml";
import { FormRenderer } from "@web/views/form/form_renderer";
import { TranscriptBox } from "./transcript_box";

export const SESSION_MODEL = "flightdeck.session";

function compileTranscriptPanel() {
    const hook = createElement("div");
    hook.classList.add("o_attachment_preview", "o_fd_transcript_pane");
    const box = createElement("t");
    setAttributes(box, {
        "t-component": "__comp__.fdTranscriptBox",
        resId: "__comp__.props.record.resId or false",
        class: "'fd_box_aside'",
    });
    append(hook, box);
    return hook;
}

registry.category("form_compilers").add("fd_transcript_panel", {
    selector: "div.o_fd_transcript",
    fn: compileTranscriptPanel,
});

patch(FormRenderer.prototype, {
    setup() {
        super.setup();
        this.fdTranscriptBox = TranscriptBox;
    },

    /** COMBO is the layout that puts a side pane beside a full-width chatter.
     * Odoo reaches it only when the record carries a real attachment, and a
     * transcript is not an attachment — so the session form asks for it
     * directly. Below the XXL breakpoint the answer stays BOTTOM_CHATTER and
     * the pane is not rendered, which is how the attachment viewer behaves too.
     */
    mailLayout(hasAttachmentContainer) {
        const layout = super.mailLayout(hasAttachmentContainer);
        if (this.props.record?.resModel !== SESSION_MODEL) {
            return layout;
        }
        return layout === "SIDE_CHATTER" ? "COMBO" : layout;
    },
});
