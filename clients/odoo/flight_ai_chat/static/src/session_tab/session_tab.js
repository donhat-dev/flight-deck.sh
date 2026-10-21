/** @odoo-module **/

import { Component } from '@odoo/owl';

import { _t } from '@web/core/l10n/translation';
import { registry } from '@web/core/registry';

import { flightViewContextLabel } from '@flight_ai_chat/view_context_label';
import { FlightToolsPopover } from '@flight_ai_chat/tools_popover/tools_popover';

/**
 * The full chat's Session tab (FC-2): the pinned screen in words and the
 * same per-chat tool switches the popup shows.
 *
 * Rendered by `muk_ai.ChatArtifactsPanel` with `{items, session, onOpenAttachment}`.
 */
export class FlightSessionTab extends Component {
    static template = 'flight_ai_chat.FlightSessionTab';
    static components = { FlightToolsPopover };
    static props = {
        items: { type: Array },
        session: { type: Object, optional: true },
        onOpenAttachment: { type: Function, optional: true },
    };

    get sessionId() {
        return (
            (this.props.items[0] && this.props.items[0].sessionId) ||
            (this.props.session && this.props.session.state.sessionId)
        );
    }

    get agentId() {
        return this.props.session ? this.props.session.state.agentId : null;
    }

    get screenLabel() {
        const ctx = this.props.session && this.props.session.state.viewContext;
        return flightViewContextLabel(ctx) || _t('No screen attached');
    }
}

registry.category('muk_ai.artifact_types').add('flight_session', {
    id: 'flight_session',
    label: _t('Session'),
    icon: 'fa-sliders',
    sequence: 5,
    component: FlightSessionTab,
    // Always non-empty while a chat is open, so the tab always shows.
    collect: (sessionState) =>
        sessionState && sessionState.sessionId
            ? [{ sessionId: sessionState.sessionId }]
            : [],
});
