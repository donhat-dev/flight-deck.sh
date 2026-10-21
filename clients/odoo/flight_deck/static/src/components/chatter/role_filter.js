/** Read a transcript by role, in the chatter and in Discuss.
 *
 * Three parts. Each turn gets a class naming its role, taken from a class the
 * turn's own body carries — `mail.message.subtype.name` is a translated field,
 * so it is the wrong thing to branch on. The chatter topbar gets a pair of
 * toggles that hide one side. Discuss gets the same pair.
 *
 * The two toolbars need two different mechanisms, and that is not a choice:
 * the chatter builds its topbar in the `mail.Chatter` template, while Discuss
 * reads the `mail.thread/actions` registry. An action registered for the
 * chatter never appears, with no error.
 *
 * The filter acts on the messages already LOADED, not on the thread. Neither
 * the chatter nor `/discuss/channel/messages` takes a domain — the channel
 * route passes `domain=None` — so filtering the fetch would mean a route of
 * our own.
 */
import { patch } from "@web/core/utils/patch";
import { reactive, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Message } from "@mail/core/common/message";
import { Chatter } from "@mail/chatter/web_portal/chatter";
import { registerThreadAction } from "@mail/core/common/thread_actions";

export const SESSION_MODEL = "flightdeck.session";
const ROLES = ["user", "assistant"];
const ROLE_MARK = "fd_role_";

// Shared between the turns and the toggles. Reactive so a toggle re-renders the
// turns; components subscribe through `useState`.
export const roleFilter = reactive({ user: true, assistant: true });

function roleOf(body) {
    return ROLES.find((role) => (body || "").includes(ROLE_MARK + role)) || null;
}

/** True for a thread whose loaded turns come from a transcript. */
function isTranscript(thread) {
    if (!thread) {
        return false;
    }
    if (thread.model === SESSION_MODEL) {
        return true;
    }
    return Boolean(thread.messages?.some((message) => roleOf(message.body)));
}

patch(Message.prototype, {
    setup(...args) {
        super.setup(...args);
        this.fdRoles = useState(roleFilter);
    },

    get attClass() {
        const base = super.attClass;
        const role = roleOf(this.message.body);
        if (!role) {
            return base;
        }
        return {
            ...base,
            [`o-fd-turn-${role}`]: true,
            "o-fd-turn-hidden": !this.fdRoles[role],
        };
    },
});

patch(Chatter.prototype, {
    setup(...args) {
        super.setup(...args);
        this.fdRoles = useState(roleFilter);
    },

    get fdIsSession() {
        return this.state.thread?.model === SESSION_MODEL;
    },

    fdToggleRole(role) {
        this.fdRoles[role] = !this.fdRoles[role];
    },
});

// Discuss: the same two toggles, through the registry the chatter does not use.
function registerRoleToggle(role, label, icon) {
    registerThreadAction(`fd-role-${role}`, {
        condition: ({ thread }) => thread?.model === "discuss.channel" && isTranscript(thread),
        icon,
        // A fixed label. A `name` computed from our own reactive state goes
        // stale: the toolbar re-renders on the framework's own action state,
        // not on state an action closes over, so the button would keep saying
        // "Hide" after it had already hidden.
        name: label,
        open: () => {
            roleFilter[role] = !roleFilter[role];
        },
        displayActive: () => roleFilter[role],
        sequence: role === "user" ? 10 : 11,
        sequenceGroup: 15,
    });
}

registerRoleToggle("user", _t("User turns"), "fa fa-fw fa-user");
registerRoleToggle("assistant", _t("Claude turns"), "fa fa-fw fa-terminal");
