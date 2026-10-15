import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { chromium } from "playwright";
import {
  valuationView,
  exportRows,
  valuationCSV,
  valuationHTML,
} from "../valuation.js";
import { localSite } from "./local-site.mjs";

const data = JSON.parse(await fs.readFile("demo.json", "utf8"));
const success = data.results[3],
  pending = data.results[4];
const model = valuationView(success, data.package);
assert.equal(
  model.entries.filter((entry) => entry.formula_code === "ttm_from_ytd").length,
  4,
);
assert.deepEqual(model.problems, []);
assert.ok(
  model.entries
    .filter((entry) => entry.formula_code === "ttm_from_ytd")
    .every((entry) => entry.label.includes("归母")),
);
const adjustedLabel = structuredClone(success);
adjustedLabel.calculations.find(
  (entry) => entry.formula_code === "ttm_from_ytd",
).parameters.metric = "net_profit_parent_excl_nonrecurring_ttm";
assert.ok(
  valuationView(adjustedLabel).entries.some((entry) =>
    entry.label.includes("扣非归母"),
  ),
);
const rows = exportRows(model);
for (const name of ["low", "base", "high"]) {
  const row = rows.find(
    (item) => item.kind === "估值结果（计算得出）" && item.id === name,
  );
  const calculation = success.calculations.find(
    (item) => item.parameters.valuation_scenario === name,
  );
  if (calculation)
    assert.equal(row.input_calculation_ids, calculation.calculation_id);
  else assert.match(row.reason, /未读取唯一/);
}
for (const calculation of success.calculations) {
  const row = rows.find(
    (item) =>
      item.kind === "计算得出" && item.id === calculation.calculation_id,
  );
  assert.deepEqual(JSON.parse(row.parameters), calculation.parameters);
}
const reviewed = structuredClone(success);
reviewed.warnings.push(
  "Earnings sustainability and historical version need review",
);
reviewed.review_items.push({
  review_id: "review_export_warning",
  target_id: success.run_id,
  status: "pending",
  reason: "Do not treat snapshot adoption as valuation approval",
  evidence_ids: ["ev_export_source"],
});
const reviewedRows = exportRows(valuationView(reviewed));
assert.ok(
  reviewedRows.some(
    (row) => row.kind === "运行警告" && row.reason === reviewed.warnings.at(-1),
  ),
);
assert.ok(
  reviewedRows.some(
    (row) =>
      row.kind === "人工复核项" && row.evidence_ids === "ev_export_source",
  ),
);
assert.ok(
  valuationCSV(valuationView(reviewed)).includes(reviewed.warnings.at(-1)),
);
assert.ok(
  valuationCSV(valuationView(reviewed)).includes(
    reviewed.review_items.at(-1).reason,
  ),
);
assert.ok(
  valuationHTML(valuationView(reviewed)).includes(
    reviewed.review_items.at(-1).reason,
  ),
);
const malformed = structuredClone(success);
malformed.calculations[0].input_calculation_ids.push("calc_missing");
assert.ok(
  valuationView(malformed).problems.some((item) => item.includes("缺少")),
);
malformed.calculations[0].input_calculation_ids = [
  malformed.calculations[0].calculation_id,
];
assert.ok(
  valuationView(malformed).problems.some((item) => item.includes("循环")),
);
assert.match(valuationCSV(valuationView(malformed)), /计算链需复核/);
const xss = structuredClone(success);
xss.calculations[0].reason = '<script>alert("bad")</script>';
assert.ok(!valuationHTML(valuationView(xss)).includes("<script>"));
assert.ok(valuationHTML(valuationView(xss)).includes("&lt;script&gt;"));
xss.calculations[0].reason = '=HYPERLINK("bad")';
assert.ok(valuationCSV(valuationView(xss)).includes("'=HYPERLINK"));
const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
try {
  const context = await browser.newContext({ acceptDownloads: true });
  await localSite(context);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("http://127.0.0.1:8765");
  await page.locator(".card").first().waitFor();
  await page.locator("#sample-result").selectOption("3");
  await page.locator(".ttm-card").first().waitFor();
  assert.equal(await page.locator(".ttm-card").count(), 4);
  assert.equal(await page.locator(".ttm-card .fact-input").count(), 12);
  assert.match(
    await page.locator("#summary").textContent(),
    /1200.*1500.*2400/,
  );
  assert.match(
    await page.locator("#results").textContent(),
    /可比性仍待人工审查/,
  );
  const first = page
    .locator(".ttm-card")
    .filter({ hasText: "fact_demo_999999_annual" });
  assert.match(await first.textContent(), /2025-07-01 至 2026-06-30/);
  await first.getByRole("button", { name: "查看原始证据" }).first().click();
  await page.locator("#location-note").filter({ hasText: "TXT" }).waitFor();
  assert.match(
    await page.locator("#evidence-meta").textContent(),
    /SHA-256 [a-f0-9]{64}/,
  );
  assert.match(await page.locator("#evidence-text").textContent(), /999999/);
  await page.screenshot({
    path: "../demo/output/c-ttm-source.png",
    fullPage: true,
  });
  for (const format of ["html", "csv"]) {
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export-" + format).click();
    const download = await downloadPromise;
    const target = `../demo/output/c-ttm-export.${format}`;
    await download.saveAs(target);
    const exported = await fs.readFile(target, "utf8");
    assert.match(exported, /接口演示样例/);
    assert.match(exported, /计算得出/);
    assert.match(exported, /fact_demo_999999_annual/);
    assert.match(exported, /fact_demo_999999_current/);
    assert.match(exported, /fact_demo_999999_prior/);
    assert.match(exported, /sha256=/);
  }
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export").click();
  const download = await downloadPromise;
  await download.saveAs("../demo/output/c-ttm-export.json");
  assert.deepEqual(
    JSON.parse(await fs.readFile("../demo/output/c-ttm-export.json", "utf8"))
      .calculations,
    success.calculations,
  );
  await page.locator("#sample-result").selectOption("4");
  await page
    .locator("#results")
    .filter({ hasText: "Missing recent target" })
    .waitFor();
  assert.equal(
    (await page.locator("#summary").textContent()).split("暂无结果").length - 1,
    3,
  );
  assert.equal(await page.locator(".ttm-card").count(), 3);
  await page.screenshot({
    path: "../demo/output/c-ttm-pending.png",
    fullPage: true,
  });
  // 真实模式不得使用缓存的合成事实填补缺失的生产读取接口。
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/result")
      ? success
      : path.endsWith("/events")
        ? { events: [], next_cursor: null }
        : { run_id: success.run_id, status: "needs_review" };
    await route.fulfill({ json: body });
  });
  await page.getByRole("button", { name: "连接团队 API" }).click();
  await page.locator("#api-base").fill("http://127.0.0.1:8765/api/v1");
  await page.locator("#task-type").selectOption("valuation");
  await page
    .locator("#document-ids")
    .fill(success.input_document_ids.join("\n"));
  await page.locator("#run").click();
  await page.waitForFunction(() => !document.getElementById("export").disabled);
  assert.match(await first.textContent(), /未读取原始金额/);
  assert.equal(
    await first.getByRole("button", { name: "查看原始证据" }).count(),
    0,
  );
  await page.getByRole("button", { name: "离线样例", exact: true }).click();
  await page.locator("#sample-result").selectOption("3");
  await page.locator(".ttm-card").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  assert.deepEqual(errors, []);
  console.log(
    "TTM browser and export checks passed: upstream facts/evidence, derived labels, pending state, sample isolation, escaping, complete chain and mobile layout",
  );
} finally {
  await browser.close();
}
