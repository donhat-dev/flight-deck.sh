// Opens the Feedback screens and saves a screenshot of each.
// NODE_PATH=$(npm root -g) node clients/odoo/tests/check_feedback_views.js <out dir>
const { chromium } = require("playwright");

const BASE = "http://localhost:8029";
const out = process.argv[2] || ".";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(BASE + "/web/login");
  await page.fill("input[name=login]", "admin");
  await page.fill("input[name=password]", "admin");
  await page.click("button[type=submit]");
  await page.waitForSelector(".o_main_navbar", { state: "attached" });
  // A navigation right after login can be aborted by the web client's own
  // redirect; settle, then retry once.
  await page.waitForTimeout(1500);
  const go = async (url) => {
    try {
      await page.goto(BASE + url, { waitUntil: "commit" });
    } catch (e) {
      await page.waitForTimeout(1500);
      await page.goto(BASE + url, { waitUntil: "commit" });
    }
  };
  const shots = [
    ["feedback-labels", "/odoo/action-flight_deck.action_feedback_kind", ".o_list_view"],
    ["turns", "/odoo/action-flight_deck.action_turns", ".o_list_view"],
    ["label-check", "/odoo/action-flight_deck.action_label_check", ".o_list_view"],
  ];
  let failed = 0;
  for (const [name, url, sel] of shots) {
    await go(url);
    try {
      await page.waitForSelector(sel, { timeout: 20000 });
      await page.waitForTimeout(800);
      const rows = await page.locator(".o_data_row").count();
      const heads = await page.locator("th.o_column_sortable, th[data-name]").allInnerTexts();
      console.log(name, "ok", rows, "rows |", heads.map((h) => h.trim()).filter(Boolean).join(", "));
    } catch (e) {
      failed += 1;
      console.log(name, "FAILED", String(e).split("\n")[0]);
    }
    await page.screenshot({ path: `${out}/${name}.png` });
  }
  // The label check's form: the first prompt, with the labels as checkboxes.
  await go("/odoo/action-flight_deck.action_label_check");
  try {
    await page.waitForSelector(".o_data_row", { timeout: 20000 });
    await page.locator(".o_data_row").first().click();
    await page.waitForSelector(".o_form_view .o_field_many2many_checkboxes", { timeout: 20000 });
    const boxes = await page.locator(".o_field_many2many_checkboxes input[type=checkbox]").count();
    console.log("label-check-form ok", boxes, "checkboxes");
    await page.screenshot({ path: `${out}/label-check-form.png`, fullPage: true });
  } catch (e) {
    failed += 1;
    console.log("label-check-form FAILED", String(e).split("\n")[0]);
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
