/** @odoo-module **/

import { onWillUnmount } from '@odoo/owl';

import { patch } from '@web/core/utils/patch';
import { effect } from '@web/core/utils/reactive';

import { WorkflowEditorApp } from '@workflow_studio/app/workflow_editor_app';

const FLIGHT_CTX_DEBOUNCE_MS = 400;

/**
 * The editor as screen context (FC-3). `useEffect` would not re-run on a
 * canvas selection (the template never reads it) and misses the async flow
 * name, so `effect` observes the reactive state as `_startPresence` does —
 * which also catches a chat window opened while the editor is showing,
 * without patching `chatWindow.open`.
 *
 * `effect(cb, deps)` re-fires only for values read through the proxies it
 * passes as arguments, and it returns nothing: every dependency is read on
 * those arguments before any early return, and the unmounted flag is the
 * stop (checked before any read, so a post-unmount notification detaches
 * this observer from the singleton `chatWindow.state`).
 */
patch(WorkflowEditorApp.prototype, {
    setup() {
        super.setup(...arguments);
        this._flightCtx = { timer: null, unmounted: false, sent: new Map() };
        const chatWindow = this.env.services['muk_ai.chat_window'];
        const deps = [
            this.state,
            this.workflowInfo,
            this.editorService && this.editorService.state && this.editorService.state.ui,
            chatWindow && chatWindow.state,
        ].filter(Boolean);
        effect((state, info, ui, chatState) => {
            if (this._flightCtx.unmounted) {
                return;
            }
            this._flightSyncViewContext({
                loading: !!(state && state.loading),
                error: state && state.error,
                view: state && state.view,
                flowName: (info && info.name) || '',
                selectedIds: [
                    ...((ui && ui.selection && ui.selection.nodeIds) || []),
                ],
                sessionIds: ((chatState && chatState.windows) || []).map(
                    (entry) => entry.sessionId,
                ),
            });
        }, deps);
        onWillUnmount(() => {
            this._flightCtx.unmounted = true;
            clearTimeout(this._flightCtx.timer);
        });
    },

    _flightSyncViewContext({ loading, error, view, flowName, selectedIds, sessionIds }) {
        const flight = this._flightCtx;
        if (!flight || flight.unmounted) {
            return;
        }
        for (const id of [...flight.sent.keys()]) {
            if (!sessionIds.includes(id)) {
                flight.sent.delete(id);
            }
        }
        if (
            !sessionIds.length ||
            loading ||
            error ||
            view !== 'editor' ||
            !this.workflowId
        ) {
            return;
        }
        const payload = this._flightViewContextPayload(flowName, selectedIds);
        const chatWindow = this.env.services['muk_ai.chat_window'];
        if (!payload || !chatWindow) {
            return;
        }
        clearTimeout(flight.timer);
        flight.timer = setTimeout(() => {
            if (flight.unmounted) {
                return;
            }
            const orm = this.env.services.orm;
            const caller = (orm && orm.silent) || orm;
            if (!caller) {
                return;
            }
            // One dedup key per session: a window that already has this
            // payload is not called again; a new one gets it once.
            const key = JSON.stringify(payload);
            for (const sessionId of chatWindow.sessionIds || []) {
                if (flight.sent.get(sessionId) === key) {
                    continue;
                }
                flight.sent.set(sessionId, key);
                caller
                    .call('muk_ai.session', 'set_view_context', [sessionId, payload])
                    .catch(() => flight.sent.delete(sessionId));
            }
        }, FLIGHT_CTX_DEBOUNCE_MS);
    },

    _flightViewContextPayload(flowName, selectedIds) {
        const nodes = this.editorService.state.graph.nodes || [];
        const selected = selectedIds
            .map((id) => nodes.find((node) => node.id === id))
            .filter(Boolean)
            .slice(0, 5)
            .map((node) => ({
                id: String(node.id),
                type: String(node.type || ''),
                label: String(node.title || node.label || node.id),
            }));
        return {
            kind: 'client_action',
            tag: 'workflow_studio.editor_app',
            title: flowName || '',
            model: 'ir.workflow',
            id: this.workflowId,
            selected_steps: selected,
        };
    },
});
