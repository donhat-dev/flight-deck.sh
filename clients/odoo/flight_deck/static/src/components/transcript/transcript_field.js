/** The transcript field, rendered by the shared chat box.
 *
 * The widget keeps its name so the arch does not change; the rendering lives in
 * `TranscriptBox`, which the form's side pane mounts too.
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";
import { TranscriptBox } from "../transcript_panel/transcript_box";

export class TranscriptField extends Component {
    static template = "flight_deck.TranscriptField";
    static components = { TranscriptBox };
    static props = { ...standardFieldProps };

    get resId() {
        return this.props.record.resId || false;
    }
}

registry.category("fields").add("fd_transcript", {
    component: TranscriptField,
    supportedTypes: ["one2many"],
});
