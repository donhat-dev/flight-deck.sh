/** Trends > Charts: the three trend charts on a page of their own. */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { TrendPanel } from "./trend_chart";
import { browserZone } from "./trend_format";

export class TrendsAction extends Component {
    static template = "flight_deck.TrendsAction";
    static components = { TrendPanel };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({ data: null, days: 30 });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call(
            "flightdeck.session", "fd_dashboard", [browserZone(), this.state.days]);
    }

    async setDays(days) {
        this.state.days = days;
        await this.load();
    }
}

registry.category("actions").add("flight_deck.trends", TrendsAction);
