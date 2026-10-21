/** Shorten the loop between changing the module and seeing it.
 *
 * Three levers, and they split along one line: an arch travels by RPC so it can
 * be swapped in place, while an asset bundle is code the browser has already
 * evaluated and only a reload can replace.
 *
 * 1. A view arch that changed re-renders the current action through
 *    `soft_reload`, with no page load. The server says so on the bus, so a form
 *    left open reacts too, not only the next view that is opened.
 * 2. `bundle_changed` reloads at once instead of asking, which is what Odoo's
 *    own watchdog does after a 10 to 60 second wait.
 * 3. The service worker's cached shell is dropped first, so the reload cannot
 *    serve the page that was there before the change.
 */
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { rpcBus } from "@web/core/network/rpc";

const ARCH_DEBOUNCE = 400;

// Set when the service starts; the debounce below has no env of its own.
let softReload = null;
let archTimer = null;

function archChanged() {
    if (!softReload) {
        return;
    }
    browser.clearTimeout(archTimer);
    archTimer = browser.setTimeout(() => softReload(), ARCH_DEBOUNCE);
}

async function dropShellCache() {
    try {
        const names = await browser.caches?.keys();
        await Promise.all((names || []).map((name) => browser.caches.delete(name)));
    } catch {
        // A browser without the Cache API just reloads over the network.
    }
}

export const liveReloadService = {
    dependencies: ["bus_service", "action", "notification"],

    start(env, { bus_service, action, notification }) {
        softReload = () => {
            notification.add(_t("The view changed. Reloading it."), { type: "info" });
            action.doAction("soft_reload");
        };

        bus_service.subscribe("view_changed", () => {
            // The arch cache hands back the OLD arch first, so re-running the
            // action without dropping it renders the same form again. The
            // cache is keyed by the RPC method, and `CLEAR-CACHES` is the
            // channel the framework listens on.
            rpcBus.dispatchEvent(new CustomEvent("CLEAR-CACHES", { detail: ["get_views"] }));
            archChanged();
        });

        // Odoo's own watchdog ignores this unless the Odoo VERSION changed —
        // `bundle_changed` carries `release.version`, which a module rebuild
        // never moves. Here any rebuilt bundle counts.
        bus_service.subscribe("bundle_changed", async () => {
            await dropShellCache();
            browser.location.reload();
        });
        bus_service.start();
    },
};

// `force` because this takes the place of Odoo's own watchdog, which waits and
// then asks.
registry.category("services").add("assetsWatchdog", liveReloadService, { force: true });
