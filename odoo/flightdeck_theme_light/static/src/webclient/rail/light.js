/** What the light rail needs that the dark one does not.
 *
 * Three things. The rail opens with the top bar already gone, because here the
 * rail is the navigation rather than a second copy of it. It can say which app
 * a row is, so each app's own sections sit under that app instead of in a zone
 * of their own. And the systray reads downwards in the order the registry
 * declares it — the account first, the messages last — rather than in the
 * order the horizontal bar needs, which is that one reversed.
 */
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { AppsBar } from "@muk_web_appsbar/webclient/appsbar/appsbar";

const systrayRegistry = registry.category("systray");

const NO_NAVBAR = "fd_no_navbar";
const STORE_KEY = "flightdeck_theme.no_navbar";

// Only when nobody has chosen: a person who turned the bar back on keeps it.
// Set before the first paint, on <html>, which exists this early — <body> does
// not, and a throw here would take the whole bundle down.
try {
    if (window.localStorage.getItem(STORE_KEY) === null) {
        document.documentElement.classList.add(NO_NAVBAR);
    }
} catch {
    document.documentElement.classList.add(NO_NAVBAR);
}

patch(AppsBar.prototype, {
    isCurrentApp(app) {
        const current = this.appMenuService.getCurrentApp();
        return !!current && current.id === app.id;
    },

    /** Zen is the state, the hidden bar is the mechanism.
     *
     * The same switch either way. Named for what the person is asking for
     * rather than for the thing they are undoing, because in this theme the
     * bar is off to begin with and "bring it back" describes nobody's intent.
     */
    get zenOn() {
        return this.navbarOff;
    },

    /** Registry order, kept.
     *
     * The registry declares the systray by sequence: the user menu at 0, the
     * company at 1, activities at 20, messages at 25. The navbar reverses that
     * because it reads right to left from the far end of the bar. A column
     * reads top to bottom from the start, so it wants the sequence as written.
     */
    get systrayItems() {
        return systrayRegistry
            .getEntries()
            .map(([key, value]) => ({ key, ...value }))
            .filter((item) => ("isDisplayed" in item ? item.isDisplayed(this.env) : true));
    },
});
