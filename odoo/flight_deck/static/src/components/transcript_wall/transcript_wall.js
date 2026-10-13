/** Several transcripts next to each other, in rows of columns.
 *
 * Reading one session against another is the thing a form cannot do: a form
 * shows one record. This is the same transcript box the form uses, mounted once
 * per column, so every turn, tool call and picture renders identically and the
 * two can never drift.
 *
 * A column takes an equal share of its row unless it has been dragged, so two
 * sessions are half the screen each and three are a third, without anyone
 * setting a size. The dashed square at the end of a row is where the next one
 * goes.
 *
 * The wall remembers what was open in the browser it was opened in. Nothing
 * about it is stored on the server, because which transcripts a person happens
 * to be comparing is not a fact about the session.
 */
import { Component, onWillStart, useExternalListener, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { TranscriptBox } from "../transcript_panel/transcript_box";

const SESSION_MODEL = "flightdeck.session";
const AGENT_MODEL = "flightdeck.session.agent";
const STORE_KEY = "flight_deck.wall";
const SEARCH_LIMIT = 12;
// Wide enough for a tool call to read as one. Mirrored by min-width in the
// stylesheet, which is what makes a row scroll sideways instead of squeezing
// every column past reading.
const MIN_WIDTH = 320;
// Three transcripts is what a wide screen reads at once; a fourth is narrower
// than a tool call and nobody reads it.
const MAX_COLUMNS = 3;

export class TranscriptWall extends Component {
    static template = "flight_deck.TranscriptWall";
    static components = { TranscriptBox };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({ rows: [], query: {}, results: {}, agents: {} });
        this.seq = 0;
        this.drag = null;
        // On the window, not on the divider: a drag that gets ahead of the
        // pointer leaves a strip eight pixels wide at the first fast movement,
        // and the grip would stop hearing about it.
        useExternalListener(window, "pointermove", (ev) => this.onDrag(ev));
        useExternalListener(window, "pointerup", () => this.endDrag());
        useExternalListener(window, "pointercancel", () => this.endDrag());
        onWillStart(() => this.restore());
    }

    // --- what is open --------------------------------------------------------
    /** The rows this browser had open, or the ones the caller asked for. */
    async restore() {
        const asked = (this.props.action || {}).context || {};
        let saved = asked.fd_panes;
        if (!saved) {
            try {
                saved = JSON.parse(window.localStorage.getItem(STORE_KEY) || "null");
            } catch {
                saved = null;
            }
        }
        const rows = this.asRows(saved);
        if (!rows.length) {
            this.addRow();
            return;
        }
        const ids = [];
        for (const row of rows) {
            for (const pane of row) {
                if (pane.session_id) {
                    ids.push(pane.session_id);
                }
            }
        }
        const names = {};
        if (ids.length) {
            for (const found of await this.orm.read(SESSION_MODEL, ids, ["name"])) {
                names[found.id] = found.name;
            }
        }
        for (const savedRow of rows) {
            const row = { key: ++this.seq, panes: [] };
            for (const pane of savedRow) {
                // A session removed since the wall was last open takes its
                // column with it rather than leaving one that can never load.
                if (pane.session_id && !names[pane.session_id]) {
                    continue;
                }
                row.panes.push({
                    key: ++this.seq,
                    sessionId: pane.session_id || false,
                    sessionName: names[pane.session_id] || "",
                    agentId: pane.agent_id || false,
                    agentName: pane.agent_name || "",
                    width: pane.width || null,
                });
            }
            this.state.rows.push(row);
        }
        if (!this.state.rows.length) {
            this.addRow();
        }
    }

    /** Stored layouts, old and new, as a list of rows of columns.
     *
     * The wall was one row of columns before it was rows, and a browser that
     * was open then still holds that flat list.
     */
    asRows(saved) {
        if (!Array.isArray(saved) || !saved.length) {
            return [];
        }
        return Array.isArray(saved[0]) ? saved.filter((row) => row.length) : [saved];
    }

    save() {
        const rows = this.state.rows.map((row) => row.panes.map((pane) => ({
            session_id: pane.sessionId || false,
            agent_id: pane.agentId || false,
            agent_name: pane.agentName || "",
            width: pane.width || null,
        })));
        try {
            window.localStorage.setItem(STORE_KEY, JSON.stringify(rows));
        } catch {
            // A browser with storage turned off still gets a working wall; it
            // just opens empty next time.
        }
    }

    addRow() {
        const row = { key: ++this.seq, panes: [] };
        this.state.rows.push(row);
        this.addPane(row);
    }

    closeRow(row) {
        const at = this.state.rows.indexOf(row);
        if (at !== -1) {
            this.state.rows.splice(at, 1);
        }
        for (const pane of row.panes) {
            this.forget(pane);
        }
        if (!this.state.rows.length) {
            this.addRow();
        }
        this.save();
    }

    addPane(row, from) {
        if (row.panes.length >= MAX_COLUMNS) {
            return;
        }
        row.panes.push({
            key: ++this.seq,
            // Splitting an open column opens the same transcript beside itself,
            // which is how one compares the top of a session with its end.
            sessionId: (from && from.sessionId) || false,
            sessionName: (from && from.sessionName) || "",
            agentId: false,
            agentName: "",
            // No width until someone drags one: a column takes its share.
            width: null,
        });
        this.save();
    }

    closePane(row, pane) {
        const at = row.panes.indexOf(pane);
        if (at !== -1) {
            row.panes.splice(at, 1);
        }
        this.forget(pane);
        if (!row.panes.length && this.state.rows.length > 1) {
            this.closeRow(row);
            return;
        }
        this.save();
    }

    forget(pane) {
        delete this.state.query[pane.key];
        delete this.state.results[pane.key];
        delete this.state.agents[pane.key];
    }

    // --- sizes ---------------------------------------------------------------
    /** A column is a share of its row until it is dragged, then it is pixels.
     *
     * The last column of a row grows from its pixels rather than sitting at
     * them, so whatever the others leave over is taken up instead of showing as
     * a gap — after a drag, after a column is closed, and when the window is
     * resized under a row whose widths were set at another size.
     *
     * Both branches state a min-width, and the pinned one has to. A flex item
     * defaults to `min-width: auto`, which is the width of its widest
     * unbreakable content — one long line in a tool call put a column at
     * 28,906 pixels while its own style said 525.
     */
    paneStyle(pane, last) {
        if (!pane.width) {
            return "flex: 1 1 0; min-width: " + MIN_WIDTH + "px";
        }
        const grow = last ? "1 1 " : "0 0 ";
        return "flex: " + grow + pane.width + "px; min-width: 0";
    }

    /** The row's own height.
     *
     * One row fills the page, because there is nothing below it to look at.
     * More than one and each is short enough to show that the next one is
     * there, which is the whole reason for having rows.
     */
    rowStyle() {
        return this.state.rows.length > 1 ? "height: 80vh" : "height: 100%";
    }

    full(row) {
        return row.panes.length >= MAX_COLUMNS;
    }

    /** The dashed square is an invitation, and it stops being one as soon as
     * the row is a comparison. A row already holding two says what to do with
     * the third through the button beside it. */
    inviting(row) {
        return row.panes.length < 2;
    }

    get paneCount() {
        return this.state.rows.reduce((n, row) => n + row.panes.length, 0);
    }

    // --- choosing what a column shows ----------------------------------------
    async search(pane, term) {
        this.state.query[pane.key] = term;
        const domain = term ? [["name", "ilike", term]] : [];
        this.state.results[pane.key] = await this.orm.searchRead(
            SESSION_MODEL, domain, ["name", "runtime_state", "text_count"],
            { limit: SEARCH_LIMIT, order: "write_date desc" });
    }

    async pick(pane, found) {
        pane.sessionId = found.id;
        pane.sessionName = found.name || "";
        pane.agentId = false;
        pane.agentName = "";
        this.state.results[pane.key] = null;
        this.state.query[pane.key] = "";
        this.state.agents[pane.key] = null;
        this.save();
    }

    /** The agents of this column's session that wrote a file worth opening. */
    async loadAgents(pane) {
        if (!pane.sessionId || this.state.agents[pane.key]) {
            return;
        }
        this.state.agents[pane.key] = await this.orm.searchRead(
            AGENT_MODEL,
            [["session_ref_id", "=", pane.sessionId], ["transcript_path", "!=", false]],
            ["description", "status"], { order: "start_ts, id" });
    }

    onAgentChange(pane, value) {
        const wanted = parseInt(value, 10);
        const agents = this.state.agents[pane.key] || [];
        return this.showAgent(pane, agents.find((agent) => agent.id === wanted));
    }

    /** Show one agent's own transcript in this column, or the session again.
     *
     * The file is read here rather than on mount: an agent nobody ever opened
     * has no rows in the database, and the box would draw an empty column.
     */
    async showAgent(pane, agent) {
        if (agent) {
            await this.orm.call(AGENT_MODEL, "action_load_transcript", [[agent.id]]);
        }
        pane.agentId = agent ? agent.id : false;
        pane.agentName = agent ? agent.description || "Agent" : "";
        this.save();
    }

    title(pane) {
        if (!pane.sessionId) {
            return "Choose a session";
        }
        return pane.agentId ? pane.agentName : pane.sessionName || "Session";
    }

    // --- the divider ---------------------------------------------------------
    /** Move the boundary between the two columns the divider sits between.
     *
     * Both of them, because the row has a width of its own: taking pixels off
     * the left column without giving them to the right one leaves the row
     * short by exactly what was dragged, which reads as a gap at the end.
     *
     * Every column is pinned at its drawn width first. Otherwise the ones still
     * taking a share would re-divide what is left on each pointer move, and the
     * column being dragged would slide away from the pointer.
     */
    startDrag(ev, row, pane) {
        ev.preventDefault();
        for (const other of row.panes) {
            if (!other.width) {
                other.width = Math.round(this.drawnWidth(other));
            }
        }
        const right = row.panes[row.panes.indexOf(pane) + 1];
        if (!right) {
            return;
        }
        this.drag = {
            left: pane, right,
            x: ev.clientX,
            leftWidth: pane.width, rightWidth: right.width,
        };
    }

    drawnWidth(pane) {
        const found = document.querySelector('[data-pane="' + pane.key + '"]');
        return found ? found.getBoundingClientRect().width : MIN_WIDTH;
    }

    onDrag(ev) {
        if (!this.drag) {
            return;
        }
        const { left, right, leftWidth, rightWidth } = this.drag;
        // Neither side may be dragged below the reading width, so the boundary
        // stops rather than the column it is pushing.
        const moved = Math.min(
            Math.max(ev.clientX - this.drag.x, MIN_WIDTH - leftWidth),
            rightWidth - MIN_WIDTH);
        left.width = leftWidth + moved;
        right.width = rightWidth - moved;
    }

    endDrag() {
        if (!this.drag) {
            return;
        }
        this.drag = null;
        this.save();
    }

    /** Give every column in the row its share back. */
    evenOut(row) {
        for (const pane of row.panes) {
            pane.width = null;
        }
        this.save();
    }
}

registry.category("actions").add("flight_deck.transcript_wall", TranscriptWall);
