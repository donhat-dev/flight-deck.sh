// Opens the Orchestrator page's Supervisor section, the Watches list and form, and the Ledger.
// NODE_PATH=$(npm root -g) node clients/odoo/tests/check_orchestrator_supervisor_views.js <out dir>
const { chromium } = require("playwright");

const BASE = "http://localhost:8029";
const out = process.argv[2] || ".";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(BASE + "/web/login");
  await page.fill("input[name=login]", "admin");
  await page.fill("input[name=password]", "admin");
  await page.click("button[type=submit]");
  await page.waitForSelector(".o_main_navbar", { state: "attached" });
  await page.waitForTimeout(1500);
  let failed = 0;
  const step = async (name, fn) => {
    try {
      console.log(name, "ok", await fn());
    } catch (e) {
      failed += 1;
      console.log(name, "FAILED", String(e).split("\n")[0]);
    }
    await page.screenshot({ path: `${out}/${name}.png` });
  };

  await step("supervisor_panel", async () => {
    await page.goto(BASE + "/odoo/action-flight_deck.action_orchestrator", { waitUntil: "commit" });
    await page.waitForSelector(".fd_orch_root", { timeout: 20000 });
    await page.click(".fd_orch_root");
    await page.waitForSelector(".fd_orch_panel");
    // Section headings are upper-cased by CSS, so compare without case.
    const text = (await page.locator(".fd_orch_panel").innerText()).toLowerCase();
    for (const want of ["Supervisor", "Keep the relay running", "Watches", "Ledger",
                        "Main sessions: turns that ended"]) {
      if (!text.includes(want.toLowerCase())) {
        throw new Error(`panel lacks "${want}"`);
      }
    }
    return (await page.locator(".fd_orch_panel .fd_orch_requests li").count()) + " list rows";
  });

  await step("watch_from_session", async () => {
    const session = page.locator(".fd_orch_session").first();
    await session.click();
    await page.waitForSelector("text=Watch this session");
    await page.click("text=Watch this session");
    await page.waitForSelector(".modal .o_form_view");
    const name = await page.locator(".modal .o_field_widget[name=name] input").inputValue();
    const watched = await page.locator(".modal .o_field_widget[name=session_id] input").inputValue();
    await page.click(".modal .o_form_button_cancel, .modal button.btn-close");
    return `name="${name}" watched="${watched}"`;
  });

  await step("watches_list", async () => {
    await page.goto(BASE + "/odoo/action-flight_deck.action_orchestrator_watch", { waitUntil: "commit" });
    await page.waitForSelector(".o_list_view .o_data_row");
    return (await page.locator(".o_data_row").count()) + " watches";
  });

  await step("ledger_list", async () => {
    await page.goto(BASE + "/odoo/action-flight_deck.action_orchestrator_event", { waitUntil: "commit" });
    await page.waitForSelector(".o_list_view");
    return (await page.locator(".o_data_row").count()) + " entries";
  });

  console.log("page errors:", errors.length ? errors : "none");
  await browser.close();
  process.exit(failed || errors.length ? 1 : 0);
})();
