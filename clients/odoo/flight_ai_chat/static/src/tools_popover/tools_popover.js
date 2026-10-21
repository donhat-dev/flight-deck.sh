/** @odoo-module **/

import { Component, onWillStart, useState } from '@odoo/owl';

import { _t } from '@web/core/l10n/translation';
import { useService } from '@web/core/utils/hooks';

/**
 * Per-chat tool switches (FC-1, FC-2). Shown as a popover from the popup
 * window's Tools button and inline on the full chat's Session tab.
 *
 * Reads and writes through `muk_ai.session` `flight_get_tool_settings` /
 * `flight_set_tool_filter`; a change applies to the chat's next turn.
 */
export class FlightToolsPopover extends Component {
    static template = 'flight_ai_chat.FlightToolsPopover';
    static props = {
        sessionId: { type: Number },
        agentId: { type: [Number, { value: null }, Boolean], optional: true },
        inline: { type: Boolean, optional: true },
        close: { type: Function, optional: true },
        onChange: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService('orm');
        this.notification = useService('notification');
        this.state = useState({
            loading: true,
            error: '',
            busy: false,
            search: '',
            groups: [],
            disabledCount: 0,
            server: null,
        });
        onWillStart(() => this._load());
    }

    async _load() {
        try {
            const settings = await this.orm.call(
                'muk_ai.session',
                'flight_get_tool_settings',
                [this.props.sessionId],
            );
            this._apply(settings);
        } catch (error) {
            this.state.error = this._errorText(error);
        } finally {
            this.state.loading = false;
        }
    }

    _apply(settings) {
        this.state.groups = settings.groups || [];
        this.state.disabledCount = settings.disabled_count || 0;
        this.state.server = settings.server || null;
        this.state.error = '';
        if (this.props.onChange) {
            this.props.onChange(this.state.disabledCount);
        }
    }

    _errorText(error) {
        return (
            (error && error.data && error.data.message) ||
            (error && error.message) ||
            _t('The tool list could not be loaded.')
        );
    }

    get visibleGroups() {
        const search = this.state.search.trim().toLowerCase();
        if (!search) {
            return this.state.groups;
        }
        return this.state.groups
            .map((group) => ({
                ...group,
                tools: group.tools.filter(
                    (tool) =>
                        tool.name.toLowerCase().includes(search) ||
                        (tool.description || '').toLowerCase().includes(search),
                ),
            }))
            .filter((group) => group.tools.length);
    }

    get countsLabel() {
        const total = this.state.groups.reduce(
            (sum, group) => sum + group.tools.length,
            0,
        );
        return _t('%s of %s tools on', total - this.state.disabledCount, total);
    }

    get serverLabel() {
        return (this.state.server && this.state.server.label) || '';
    }

    _disabledNames() {
        const disabled = new Set();
        for (const group of this.state.groups) {
            for (const tool of group.tools) {
                if (!tool.enabled) {
                    disabled.add(tool.name);
                }
            }
        }
        return disabled;
    }

    async _applyDisabled(disabled) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const settings = await this.orm.call(
                'muk_ai.session',
                'flight_set_tool_filter',
                [this.props.sessionId, [...disabled]],
            );
            this._apply(settings);
        } catch (error) {
            this.notification.add(this._errorText(error), { type: 'danger' });
            await this._load();
        } finally {
            this.state.busy = false;
        }
    }

    onToolToggle(tool) {
        const disabled = this._disabledNames();
        if (tool.enabled) {
            disabled.delete(tool.name);
        } else {
            disabled.add(tool.name);
        }
        this._applyDisabled(disabled);
    }

    setGroup(group, on) {
        const disabled = this._disabledNames();
        for (const tool of group.tools) {
            if (on) {
                disabled.delete(tool.name);
            } else {
                disabled.add(tool.name);
            }
        }
        this._applyDisabled(disabled);
    }
}
