/** @odoo-module **/

import { useEffect, useState } from '@odoo/owl';

import { usePopover } from '@web/core/popover/popover_hook';
import { patch } from '@web/core/utils/patch';
import { useService } from '@web/core/utils/hooks';

import { ChatWindow } from '@muk_ai/chat/window/chat_window';

import { FlightToolsPopover } from '@flight_ai_chat/tools_popover/tools_popover';

/** Tools button on the popup chat window header (FC-1). */
patch(ChatWindow.prototype, {
    setup() {
        super.setup(...arguments);
        this.orm = this.orm || useService('orm');
        this.flightToolsState = useState({ disabledCount: 0 });
        this.flightToolsPopover = usePopover(FlightToolsPopover, {
            position: 'bottom-end',
        });
        // The badge shows before the popover is ever opened, and follows an
        // agent switch: the catalogue and the deny count change with it.
        useEffect(
            () => {
                this.flightToolsPopover.close();
                this._flightLoadToolBadge();
            },
            () => [this.props.sessionId, this.session.state.agentId],
        );
    },

    onFlightTools(ev) {
        if (this.flightToolsPopover.isOpen) {
            this.flightToolsPopover.close();
            return;
        }
        this.flightToolsPopover.open(ev.currentTarget, {
            sessionId: this.props.sessionId,
            agentId: this.session.state.agentId,
            onChange: (count) => {
                this.flightToolsState.disabledCount = count;
            },
        });
    },

    get flightToolsBadge() {
        return this.flightToolsState.disabledCount || '';
    },

    async _flightLoadToolBadge() {
        try {
            const orm = this.orm.silent || this.orm;
            const settings = await orm.call(
                'muk_ai.session',
                'flight_get_tool_settings',
                [this.props.sessionId],
            );
            this.flightToolsState.disabledCount = settings.disabled_count || 0;
        } catch {
            /* a window without a readable session shows no badge */
        }
    },
});
