import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { chromium } from "playwright";
import { localSite } from "./local-site.mjs";
import {
  disclosureView,
  disclosureCSV,
  disclosureHTML,
  showValue,
} from "../disclosures.js";

const demo = JSON.parse(await fs.readFile("demo.json", "utf8"));
const extraction = structuredClone(
  demo.results.find((result) => result.task_type === "structured_extraction"),
);
const financial = structuredClone(
  demo.results.find((result) => result.task_type === "financial_analysis"),
);
const shares = extraction.data.records[0].fields.find(
  (field) => field.field_code === "pledged_shares",
);
assert.equal(shares.value, "12500000");
shares.value = "9007199254740993";
shares.raw_value = '=HYPERLINK("unsafe")';
shares.reason = '<img src=x onerror="alert(1)">';
shares.evidence_ids = ["ev_unknown"];
const view = disclosureView(extraction, true);
const projected = view.records[0].fields.find(
  (field) => field.field_code === "pledged_shares",
);
assert.deepEqual(projected.usableRefs, []);
assert.deepEqual(projected.missingRefs, ["ev_unknown"]);
assert.ok(view.issues.length);
assert.equal(projected.value, "9007199254740993");
assert.equal(showValue("0"), "0");
assert.equal(showValue(null), "未确定");
assert.equal(showValue("-8.2000"), "-8.2000");
const csv = disclosureCSV(view),
  html = disclosureHTML(view);
assert.ok(csv.includes("9007199254740993"));
assert.ok(csv.includes("'=HYPERLINK"));
assert.ok(html.includes("&lt;img"));
assert.ok(!html.includes("<img"));
assert.ok(html.includes(extraction.run_id) && html.includes("完整 TaskResult"));
const missing = structuredClone(financial);
missing.calculations[0].input_fact_ids.push("fact_not_returned");
missing.calculations[0].input_calculation_ids.push("calc_not_returned");
const missingView = disclosureView(missing);
assert.ok(
  missingView.issues.some((issue) => issue.includes("fact_not_returned")),
);
assert.ok(
  missingView.issues.some((issue) => issue.includes("calc_not_returned")),
);
assert.equal(missingView.calculations[0].inputs.at(-1).fact, undefined);
assert.ok(disclosureCSV(missingView).includes("fact_not_returned"));
const empty = structuredClone(extraction);
empty.data.records = [];
assert.equal(disclosureView(empty).records.length, 0);
assert.ok(disclosureCSV(disclosureView(empty)).includes("未返回数据"));

const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1100 },
  });
  await localSite(page.context());
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("http://127.0.0.1:8765");
  await page.locator(".card").first().waitFor();
  await page.locator("#sample-result").selectOption("8");
  await page.locator('.field-card[data-field="pledged_shares"]').waitFor();
  assert.match(
    await page
      .locator('.field-card[data-field="pledged_shares"]')
      .textContent(),
    /1,250万股/,
  );
  assert.match(
    await page
      .locator('.field-card[data-field="pledged_shares"]')
      .textContent(),
    /12500000/,
  );
  assert.match(
    await page
      .locator('.field-card[data-field="pledge_end_date"]')
      .textContent(),
    /未确定/,
  );
  assert.match(await page.locator("#summary").textContent(), /全部虚构/);
  await page
    .locator('.field-card[data-field="pledged_shares"]')
    .getByRole("button")
    .first()
    .click();
  await page
    .locator("#evidence-text")
    .filter({ hasText: "1,250万股" })
    .waitFor();
  await fs.mkdir("../demo/output", { recursive: true });
  await page.screenshot({
    path: "../demo/output/c-extraction-fields.png",
    fullPage: true,
  });
  for (const format of ["html", "csv"]) {
    const pending = page.waitForEvent("download");
    await page.locator("#export-disclosure-" + format).click();
    const download = await pending;
    const raw = await fs.readFile(await download.path(), "utf8");
    assert.ok(
      raw.includes("12500000") &&
        raw.includes("run_c_disclosure_structured_extraction"),
    );
    assert.ok(download.suggestedFilename().startsWith("synthetic-"));
  }
  await page.locator("#sample-result").selectOption("9");
  await page.locator(".financial-fact").first().waitFor();
  assert.equal(
    await page.locator(".financial-fact").count(),
    financial.data.facts.length,
  );
  assert.match(await page.locator("#results").textContent(), /合并.*累计/);
  assert.match(
    await page.locator(".financial-calculation").first().textContent(),
    /20.*%/,
  );
  await page.screenshot({
    path: "../demo/output/c-financial-facts.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 2,
    ),
  );
  await page.locator("#sample-result").selectOption("0");
  await page.locator(".card.correct").first().waitFor();
  assert.equal(
    await page.locator("#export-disclosure-html").isVisible(),
    false,
  );
  // 直接加载损坏引用和条件不足的接口形状，不能靠全局来源按钮掩盖问题。
  const edgeDemo = structuredClone(demo);
  edgeDemo.results[8] = extraction;
  const pendingFinancial = structuredClone(financial);
  pendingFinancial.data.facts[0].value = null;
  pendingFinancial.data.facts[0].review_status = "needs_review";
  pendingFinancial.data.facts[0].reason = "合成测试：原始事实尚待核实";
  pendingFinancial.data.alerts.push({
    alert_code: "synthetic_unresolved",
    severity: "high",
    description: "合成测试：现金流原因尚待核查",
    fact_ids: ["fact_not_returned"],
    evidence_ids: ["ev_unknown"],
    interpretation_status: "unresolved",
  });
  edgeDemo.results[9] = pendingFinancial;
  await page.route("**/demo.json", (route) =>
    route.fulfill({ json: edgeDemo }),
  );
  await page.locator("#sample-result").selectOption("8");
  const edgeField = page.locator('.field-card[data-field="pledged_shares"]');
  await edgeField.filter({ hasText: "9007199254740993" }).waitFor();
  assert.equal(await edgeField.getByRole("button").count(), 0);
  assert.match(await edgeField.textContent(), /已停止跳转/);
  assert.equal(await edgeField.locator("img").count(), 0);
  await page.locator("#sample-result").selectOption("9");
  await page.locator(".financial-alert").waitFor();
  assert.match(
    await page.locator(".financial-fact").first().textContent(),
    /未确定/,
  );
  assert.match(
    await page.locator(".financial-fact").first().textContent(),
    /needs_review/,
  );
  assert.match(
    await page.locator(".financial-alert").textContent(),
    /原因尚未确定/,
  );
  assert.match(
    await page.locator(".financial-alert").textContent(),
    /关联事实未提供/,
  );
  assert.equal(
    await page.locator(".financial-alert").getByRole("button").count(),
    0,
  );
  assert.deepEqual(errors, []);
  console.log(
    "Disclosure display checks passed: exact strings, null/zero, missing refs, exports, source jump and mobile",
  );
} finally {
  await browser.close();
}
