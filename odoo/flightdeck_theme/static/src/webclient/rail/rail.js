/** Two controls the app rail has no place for, and the state behind them.
 *
 * MuK already draws the rail and already knows two widths — `sidebar_type` on
 * res.users, applied as a class on the body. What it has no way to do is change
 * either of them without going to the settings page, which is not where anyone
 * is when they want the screen wider.
 *
 * So: a collapse button on the rail, and a second one that takes the top bar
 * away. The bar carries two things nothing else does — the app's own menu and
 * the systray, which is where the user menu, the company and the messages
 * live. Both move into the rail for as long as the bar is gone, so taking it
 * away puts nothing out of reach.
 */
import { patch } from "@web/core/utils/patch";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";
import { ErrorHandler } from "@web/core/utils/components";
import { AppsBar } from "@muk_web_appsbar/webclient/appsbar/appsbar";
import { onWillUnmount } from "@odoo/owl";

const systrayRegistry = registry.category("systray");

const NARROW = "mk_sidebar_type_small";
const WIDE = "mk_sidebar_type_large";
const NO_NAVBAR = "fd_no_navbar";
const STORE_KEY = "flightdeck_theme.no_navbar";

/** The top bar is a browser choice, not an account one: it is about the screen
 * in front of the person, and the same account uses several. */
function navbarHidden() {
    try {
        return window.localStorage.getItem(STORE_KEY) === "1";
    } catch {
        return false;
    }
}

function rememberNavbar(hidden) {
    try {
        window.localStorage.setItem(STORE_KEY, hidden ? "1" : "0");
    } catch {
        // A browser with storage off still gets the toggle; it just forgets.
    }
}

// The class goes on <html>, not on <body>. Backend assets are loaded from the
// document head, so at the moment this module is evaluated document.body is
// still null — and one throw here takes the whole bundle with it, not just this
// file. <html> exists from the first byte, which is also what lets the class be
// set before the first paint so the bar is never drawn and then taken away.
const root = document.documentElement;

if (navbarHidden()) {
    root.classList.add(NO_NAVBAR);
}

AppsBar.components = { ...AppsBar.components, ErrorHandler };

patch(AppsBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.menuService = this.env.services.menu;
        // A systray item can appear or go at any time — an activity counter,
        // an installed app's own button. MuK's rail only listens for the app
        // changing, which is not the same event.
        const redraw = () => this.render();
        systrayRegistry.addEventListener("UPDATE", redraw);
        onWillUnmount(() => systrayRegistry.removeEventListener("UPDATE", redraw));
    },

    get collapsed() {
        return document.body.classList.contains(NARROW);
    },

    get navbarOff() {
        return root.classList.contains(NO_NAVBAR);
    },

    /** Wider or narrower, and remembered on the account.
     *
     * The class changes first and the write follows: the rail is a hundred
     * pixels moving, and nobody should watch a round trip to get it.
     */
    toggleWidth() {
        const collapsed = this.collapsed;
        document.body.classList.toggle(NARROW, !collapsed);
        document.body.classList.toggle(WIDE, collapsed);
        this.render();
        rpc("/web/dataset/call_kw", {
            model: "res.users",
            method: "write",
            args: [[user.userId], { sidebar_type: collapsed ? "large" : "small" }],
            kwargs: {},
        }).catch(() => {
            // The look is already right; only the memory of it failed.
        });
    },

    toggleNavbar() {
        const hidden = !this.navbarOff;
        root.classList.toggle(NO_NAVBAR, hidden);
        rememberNavbar(hidden);
        this.render();
    },

    /** The active app's own menu, which the top bar carries when it is there.
     *
     * A first-level entry with no action of its own is a heading: selectMenu
     * returns without doing anything for it, so in the rail it would be a line
     * that answers no click. It becomes a heading here too, and the children
     * under it — the ones that can actually be opened — take its place.
     *
     * Two levels and no further. A rail is a rail, and a tree inside it is a
     * second navigation to learn rather than the one that was taken away.
     */
    get appMenu() {
        if (!this.navbarOff) {
            return [];
        }
        const current = this.appMenuService.getCurrentApp();
        if (!current) {
            return [];
        }
        const tree = this.menuService.getMenuAsTree(current.id);
        const items = [];
        for (const item of tree.childrenTree || []) {
            if (!item.name) {
                continue;
            }
            if (item.actionID) {
                items.push(item);
                continue;
            }
            items.push({ id: item.id, name: item.name, heading: true });
            for (const child of item.childrenTree || []) {
                if (child.actionID && child.name) {
                    items.push(child);
                }
            }
        }
        return items;
    },

    openMenu(item) {
        this.menuService.selectMenu(item);
    },

    /** The systray, on the same terms the top bar gets it.
     *
     * Read from the registry rather than copied out of the bar's DOM: these
     * are live components, and a counter that stopped counting would be worse
     * than no counter.
     */
    get systrayItems() {
        return systrayRegistry
            .getEntries()
            .map(([key, value]) => ({ key, ...value }))
            .filter((item) => ("isDisplayed" in item ? item.isDisplayed(this.env) : true))
            .reverse();
    },

    handleSystrayError(error, item) {
        item.isDisplayed = () => false;
        Promise.resolve().then(() => {
            throw error;
        });
    },
});
