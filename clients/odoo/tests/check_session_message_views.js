// Opens the session message screens and the Sessions filter of the transcript box.
// NODE_PATH=$(npm root -g) node clients/odoo/tests/check_session_message_views.js <out dir> [session id]
const { chromium } = require("playwright");

const BASE = "http://localhost:8029";
const out = process.argv[2] || ".";
const sessionId = process.argv[3] || "2735";

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
  const go = async (url) => {
    try {
      await page.goto(BASE + url, { waitUntil: "commit" });
    } catch (e) {
      await page.waitForTimeout(1500);
      await page.goto(BASE + url, { waitUntil: "commit" });
    }
  };
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

  await step("conversations", async () => {
    await go("/odoo/action-flight_deck.action_session_thread");
    await page.waitForSelector(".o_data_row", { timeout: 20000 });
    return `${await page.locator(".o_data_row").count()} rows`;
  });
  await step("conversation-form", async () => {
    await page.locator(".o_data_row").first().click();
    await page.waitForSelector(".o_form_view .o_field_one2many .o_data_row", { timeout: 20000 });
    return `${await page.locator(".o_field_one2many .o_data_row").count()} messages`;
  });
  await step("messages", async () => {
    await go("/odoo/action-flight_deck.action_session_message");
    await page.waitForSelector(".o_data_row", { timeout: 20000 });
    return `${await page.locator(".o_data_row").count()} rows`;
  });
  await step("session-form", async () => {
    await go(`/odoo/action-flight_deck.action_logbook/${sessionId}`);
    await page.waitForSelector(".fd_box_head", { timeout: 30000 });
    const stat = await page.locator("button[name=action_open_session_messages]").innerText();
    return stat.replace(/\s+/g, " ");
  });
  await step("sessions-filter", async () => {
    await page.locator(".fd_box_chip", { hasText: "Sessions" }).click();
    await page.waitForTimeout(1500);
    const roles = await page.locator(".fd_box .fd_bubble .fd_bubble_role").allInnerTexts();
    const from = await page.locator(".fd_box .fd_bubble_from").allInnerTexts();
    const count = await page.locator(".fd_box_count").innerText();
    if (!roles.length || roles.some((r) => !/peer|assistant/i.test(r))) {
      throw new Error(`unexpected roles ${roles.join(",")}`);
    }
    return `${count.trim()} | roles ${[...new Set(roles)].join(",")} | ${from[0] || "no sender shown"}`;
  });
  console.log(errors.length ? "page errors: " + errors.join(" | ") : "no page errors");
  await browser.close();
  process.exit(failed || errors.length ? 1 : 0);
})();
