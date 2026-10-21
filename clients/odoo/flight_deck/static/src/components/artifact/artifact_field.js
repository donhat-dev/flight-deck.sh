/** A rendered artifact, shown as the document it is.
 *
 * An `Html` field sanitizes what it stores, and an artifact is a whole page
 * carrying its own styles and embedded fonts. It is served from its attachment
 * and displayed in a sandboxed frame, which is also what keeps its scripts out
 * of the Odoo session.
 */
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState } from "@odoo/owl";

export class ArtifactField extends Component {
    static template = "flight_deck.ArtifactField";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ tall: false });
    }

    get url() {
        return this.props.record.data[this.props.name] || "";
    }

    toggleHeight() {
        this.state.tall = !this.state.tall;
    }
}

registry.category("fields").add("fd_artifact", {
    component: ArtifactField,
    supportedTypes: ["char"],
});
