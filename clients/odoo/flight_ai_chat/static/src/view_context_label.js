/** @odoo-module **/

import { patch } from '@web/core/utils/patch';

import { AIChat } from '@muk_ai/chat/chat';
import { viewContextLabel } from '@muk_ai/chat/session/view_context_format';
import { ChatWindow } from '@muk_ai/chat/window/chat_window';

/**
 * View-context chip label with a `client_action` case (FC-3): MuK's
 * `viewContextLabel` falls back to the raw model name for unknown kinds,
 * so a pinned editor would read "ir.workflow".
 */
export function flightViewContextLabel(ctx) {
    if (ctx && ctx.kind === 'client_action') {
        const parts = [ctx.title || ctx.tag || 'Screen'];
        const steps = (ctx.selected_steps || [])
            .slice(0, 2)
            .map((step) => step.label || step.id)
            .filter(Boolean);
        if (steps.length) {
            parts.push(steps.join(', '));
        }
        return parts.join(' · ');
    }
    return viewContextLabel(ctx);
}

patch(ChatWindow.prototype, {
    get viewContextLabel() {
        return flightViewContextLabel(this.session.state.viewContext);
    },
});

patch(AIChat.prototype, {
    get viewContextLabel() {
        return flightViewContextLabel(this.session.state.viewContext);
    },
});
