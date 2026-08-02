const { chromium } = require("playwright");
const path = require("path");

const outDir = path.resolve(__dirname, "..");
const baseUrl = process.env.FRONTEND_URL || "http://localhost:5173";

async function waitForStableState(page, selector, intervalMs = 300, stableMs = 800, timeoutMs = 20000) {
  const start = Date.now();
  let lastText = null;
  let lastChange = start;
  while (Date.now() - start < timeoutMs) {
    const text = await page.locator(selector).textContent().catch(() => "");
    if (text !== lastText) {
      lastText = text;
      lastChange = Date.now();
    }
    if (Date.now() - lastChange > stableMs) {
      return;
    }
    await page.waitForTimeout(intervalMs);
  }
}

async function run() {
  const browser = await chromium.launch({
    headless: true,
    channel: "chrome",
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();

  try {
    await page.goto(`${baseUrl}/assessment`);
    await page.waitForSelector("input[autocomplete='nickname']");
    await page.fill("input[autocomplete='nickname']", "ui-test");
    await page.locator("form.name-console button[type='submit']").click();

    await page.waitForURL(/\/assessment\/session\//, { timeout: 15000 });
    await page.waitForSelector(".transcript", { timeout: 15000 });
    await waitForStableState(page, ".transcript");

    await page.setViewportSize({ width: 1280, height: 900 });
    await page.screenshot({ path: path.join(outDir, "frontend_fix2_screenshot_wide.png"), fullPage: false });

    const answerBox = page.locator(".answer-box textarea").first();
    await answerBox.waitFor({ timeout: 10000 });
    await answerBox.click();
    await answerBox.fill("我认为当前最核心的决策问题是产品核心链路风险是否可控，以及延期对市场宣传的影响。");
    await answerBox.evaluate((el) => {
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.waitForTimeout(200);
    await page.locator("form.answer-box button[type='submit']").click();

    await page.waitForSelector(".assessment-notice", { timeout: 5000 }).catch(() => null);
    await waitForStableState(page, ".transcript", 500, 1200, 35000);

    await page.screenshot({ path: path.join(outDir, "frontend_fix2_screenshot_after_0s.png"), fullPage: false });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, "frontend_fix2_screenshot_after_1_5s.png"), fullPage: false });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, "frontend_fix2_screenshot_after_3s.png"), fullPage: false });

    await page.setViewportSize({ width: 800, height: 900 });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(outDir, "frontend_fix2_screenshot_narrow.png"), fullPage: false });

    console.log("Screenshots saved:");
    console.log("- frontend_fix2_screenshot_wide.png");
    console.log("- frontend_fix2_screenshot_after_0s.png");
    console.log("- frontend_fix2_screenshot_after_3s.png");
    console.log("- frontend_fix2_screenshot_narrow.png");
  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
