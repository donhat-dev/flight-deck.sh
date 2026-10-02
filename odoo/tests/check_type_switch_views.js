// Opens the screens the Type switch changed and saves a screenshot of each.
// NODE_PATH=$(npm root -g) node odoo/tests/check_type_switch_views.js <out dir>
const { chromium } = require("playwright");

const BASE = "http://localhost:8029";
const out = process.argv[2] || ".";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).split("\n")[0]));
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
  const check = async (name, fn) => {
    try {
      console.log(name, "ok", await fn());
    } catch (e) {
      failed += 1;
      console.log(name, "FAILED", String(e).split("\n")[0]);
    }
    await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  };

  // The review queue: Feedback chips on a card, Type buttons only when Type is to review.
  await go("/odoo/action-flight_deck.action_cost_per_task");
  await check("review-queue", async () => {
    await page.waitForSelector(".fd_task_panel .fd_task_summary b", { timeout: 20000 });
    const open = await page.locator(".fd_review").count();
    if (!open) await page.locator(".fd_task_panel button:has-text('Review queue')").click();
    await page.waitForSelector(".fd_review", { timeout: 10000 });
    const cards = await page.locator(".fd_review_card").count();
    const save = await page.locator(".fd_review_card button:has-text('Save feedback')").count();
    const set = await page.locator(".fd_review_card .fd_review_meta:has-text('Type:')").count();
    const old = await page.locator(".fd_review_card button:has-text('correction')").count();
    if (old) throw new Error("an old Type button is shown");
    return `${cards} cards, ${save} with Feedback to review, ${set} with Type set`;
  });

  await check("task-list", async () => {
    await page.waitForSelector(".o_list_view th", { timeout: 20000 });
    const heads = (await page.locator("th[data-name]").allInnerTexts()).map((h) => h.trim()).filter(Boolean);
    if (!heads.includes("Agent wrong")) throw new Error("no Agent wrong column: " + heads.join(", "));
    return heads.join(", ");
  });

  await go("/odoo/action-flight_deck.action_model_stats");
  await check("model-stats", async () => {
    await page.waitForSelector("table thead th", { timeout: 20000 });
    await page.waitForTimeout(800);
    const heads = (await page.locator("table thead th").allInnerTexts()).map((h) => h.trim());
    for (const h of ["Agent wrong", "Revisions", "Decisions", "Pulls"]) {
      if (!heads.includes(h)) throw new Error("missing column " + h);
    }
    return heads.join(", ");
  });

  if (errors.length) {
    failed += 1;
    console.log("page errors:", errors.join(" | "));
  }
  await browser.close();
  console.log(failed ? `${failed} FAILED` : "ALL OK");
  process.exit(failed ? 1 : 0);
})();
