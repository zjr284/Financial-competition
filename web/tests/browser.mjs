import assert from "node:assert/strict";
import fs from "node:fs/promises";
import crypto from "node:crypto";
import { chromium } from "playwright";
import { localSite } from "./local-site.mjs";

const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
await localSite(page.context());
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const base = "http://127.0.0.1:8765";
const demo = JSON.parse(await fs.readFile("demo.json", "utf8"));
function pdfFixture() {
  const stream = "BT /F1 18 Tf 30 220 Td (EVIDENCE TEST) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let source = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((value, i) => {
    offsets.push(Buffer.byteLength(source));
    source += `${i + 1} 0 obj\n${value}\nendobj\n`;
  });
  const xref = Buffer.byteLength(source);
  source += `xref\n0 6\n0000000000 65535 f \n${offsets
    .slice(1)
    .map((n) => String(n).padStart(10, "0") + " 00000 n \n")
    .join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(source);
}
try {
  // 样例加载未结束就切换模式：迟到的成功/失败响应都不得污染真实模式。
  for (const responseStatus of [200, 503]) {
    const racePage = await browser.newPage();
    await localSite(racePage.context());
    let release, arrived;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const requested = new Promise((resolve) => {
      arrived = resolve;
    });
    await racePage.route("**/demo.json", async (route) => {
      arrived();
      await gate;
      await route.fulfill({ status: responseStatus, json: demo });
    });
    await racePage.goto(base);
    await requested;
    await racePage.getByRole("button", { name: "连接团队 API" }).click();
    release();
    await racePage.waitForLoadState("networkidle");
    assert.equal(await racePage.locator(".card").count(), 0);
    assert.equal(await racePage.locator("#export").isDisabled(), true);
    assert.match(
      await racePage.locator("#status").textContent(),
      /填写 API 地址/,
    );
    assert.match(
      await racePage.locator("#mode-badge").textContent(),
      /团队 API/,
    );
    await racePage.close();
  }
  // 已有运行读取未完成时切换回样例：迟到成功或失败均不得污染新页面。
  for (const responseStatus of [200, 503]) {
    const racePage = await browser.newPage();
    await localSite(racePage.context());
    let release, arrived;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const requested = new Promise((resolve) => {
      arrived = resolve;
    });
    await racePage.route("**/api/v1/runs/run_saved_review", async (route) => {
      arrived();
      await gate;
      await route.fulfill({
        status: responseStatus,
        json: {
          run_id: "run_saved_review",
          task_type: "research_verification",
          status: "succeeded",
        },
      });
    });
    await racePage.goto(base);
    await racePage.locator(".card").first().waitFor();
    await racePage.locator("#live").click();
    await racePage.locator("#api-base").fill(base + "/api/v1");
    await racePage.locator("#saved-run-id").fill("run_saved_review");
    await racePage.locator("#load-existing").click();
    await requested;
    await racePage.locator("#sample").click();
    release();
    await racePage.waitForLoadState("networkidle");
    await racePage.locator(".card").first().waitFor();
    assert.equal(await racePage.locator(".card").count(), 9);
    assert.equal(
      await racePage.locator("#mode-badge").textContent(),
      "接口演示样例",
    );
    assert.ok(
      !(await racePage.locator("#status").textContent()).includes("操作失败"),
    );
    await racePage.close();
  }
  await page.goto(base);
  await page.getByText("接口演示样例", { exact: true }).waitFor();
  await page.locator(".card").first().waitFor();
  assert.equal(await page.locator(".card").count(), 9);
  await page.getByRole("button", { name: "原句定位" }).first().click();
  await page.locator("#bbox").waitFor({ state: "visible" });
  assert.match(await page.locator("#evidence-meta").textContent(), /第 1 页/);
  assert.match(await page.locator("#location-note").textContent(), /合成 TXT/);
  await fs.mkdir("../demo/output", { recursive: true });
  await page.screenshot({ path: "../demo/output/c-web.png", fullPage: true });
  await page.locator("#sample-result").selectOption("1");
  await page.locator("#status").filter({ hasText: "已完成" }).waitFor();
  assert.equal(await page.locator(".card.correct").count(), 2);
  await page.locator("#sample-result").selectOption("2");
  await page.getByText("规则暂定评分 45/100", { exact: true }).waitFor();
  const result = structuredClone(demo.results[0]);
  let runReads = 0,
    reviewRequest = null,
    createRequest = null,
    contentCorrupted = false;
  const pdf = pdfFixture(),
    hash = crypto.createHash("sha256").update(pdf).digest("hex");
  const evidence = {
    evidence_id: "ev_pdf_test",
    document_id: "doc_pdf_test",
    page: 1,
    bbox: [100, 200, 800, 300],
    text: "EVIDENCE TEST",
    extraction_method: "manual",
    confidence: 1,
  };
  const doc = {
    document_id: "doc_pdf_test",
    filename: "test.pdf",
    media_type: "application/pdf",
    sha256: hash,
    source_url: null,
    disclosure_date: null,
    ingested_at: "2026-10-10T00:00:00+08:00",
    page_count: 1,
    parse_status: "parsed",
  };
  result.data.checks[0].source_evidence_ids = ["ev_pdf_test"];
  // 浏览器测试专用路由拦截，不是可供页面展示为实际服务的生产 mock。
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body;
    if (path.endsWith("/health"))
      body = { status: "ok", api_version: "1.0.0", model_available: false };
    else if (path.endsWith("/documents") && route.request().method() === "POST")
      body = doc;
    else if (path.endsWith("/runs") && route.request().method() === "POST") {
      createRequest = route.request().postDataJSON();
      body = { run_id: result.run_id, status: "queued" };
    } else if (path.endsWith("/result")) body = result;
    else if (path.endsWith("/events")) {
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      body = cursor
        ? {
            events: [
              { event_id: "event_page_1" },
              { event_id: "event_page_2" },
            ],
            next_cursor: null,
          }
        : { events: [{ event_id: "event_page_1" }], next_cursor: "cursor/+ 2" };
    } else if (path.includes("/runs/"))
      body = {
        run_id: result.run_id,
        status: ++runReads === 1 ? "running" : "needs_review",
      };
    else if (path.endsWith("/evidence/ev_pdf_test")) body = evidence;
    else if (path.endsWith("/documents/doc_pdf_test")) body = doc;
    else if (path.endsWith("/content")) {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        body: contentCorrupted ? Buffer.from("corrupt") : pdf,
      });
      return;
    } else if (path.includes("/reviews/")) {
      reviewRequest = route.request().postDataJSON();
      body = { ...result.review_items[0], status: "accepted" };
    } else {
      await route.fulfill({ status: 404, json: { message: "Not found" } });
      return;
    }
    await route.fulfill({ status: 200, json: body });
  });
  await page.getByRole("button", { name: "连接团队 API" }).click();
  await page.locator("#api-base").fill(base + "/api/v1");
  await page.locator("#health").click();
  await page.getByText("团队 API 已连接", { exact: true }).waitFor();
  await page.locator("#upload").setInputFiles({
    name: "test.pdf",
    mimeType: "application/pdf",
    buffer: pdf,
  });
  await page.locator("#upload-button").click();
  await page.locator("#status").filter({ hasText: "文件已登记" }).waitFor();
  assert.match(
    await page.locator("#document-ids").inputValue(),
    /doc_pdf_test/,
  );
  await page.locator("#document-ids").fill("doc_c_source\ndoc_c_draft");
  await page.locator("#task-input").fill(
    JSON.stringify({
      draft_document_id: "doc_c_draft",
      company_code: "999999",
    }),
  );
  await page.locator("#run").click();
  await page.locator("#status").filter({ hasText: "待复核" }).waitFor();
  assert.equal(createRequest.api_version, "1.0.0");
  assert.equal(runReads, 2);
  await page.getByText("运行事件与版本", { exact: true }).click();
  await page.getByRole("button", { name: "加载更多运行事件" }).click();
  await page.locator("#events").filter({ hasText: "event_page_2" }).waitFor();
  assert.equal(
    JSON.parse(await page.locator("#events").textContent()).event_page.events
      .length,
    2,
  );
  assert.equal(await page.locator("#more-events").isVisible(), false);
  await page
    .getByRole("button", { name: "来源 1", exact: true })
    .first()
    .click();
  await page
    .locator("#location-note")
    .filter({ hasText: "固定 PDF 原件已校验哈希" })
    .waitFor();
  assert.ok(await page.locator("#pdf-canvas").isVisible());
  assert.ok(await page.locator("#bbox").isVisible());
  const box = await page.locator("#bbox").boundingBox(),
    canvas = await page.locator("#pdf-canvas").boundingBox();
  assert.ok(Math.abs(box.width / canvas.width - 0.7) < 0.02);
  assert.ok(Math.abs((box.y - canvas.y) / canvas.height - 0.2) < 0.02);
  await page.screenshot({
    path: "../demo/output/c-pdf-test.png",
    fullPage: true,
  });
  const review = page.locator(".review-card").first();
  await review.getByLabel("复核人").fill("browser-test");
  await review.getByLabel("复核依据").fill("Synthetic browser test");
  await review.getByRole("button", { name: "提交人工决定" }).click();
  await page.locator("#status").filter({ hasText: "人工决定已保存" }).waitFor();
  assert.equal(reviewRequest.decision, "accept");
  assert.equal(reviewRequest.corrected_value, null);
  contentCorrupted = true;
  await page
    .getByRole("button", { name: "来源 1", exact: true })
    .first()
    .click();
  await page
    .locator("#status")
    .filter({ hasText: "SHA-256 与登记版本不符" })
    .waitFor();
  assert.equal(await page.locator("#source-link").isVisible(), false);
  await page.getByRole("button", { name: "离线样例", exact: true }).click();
  await page.locator("#sample-result").selectOption("0");
  await page.locator(".card").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  assert.deepEqual(errors, []);
  console.log(
    "Browser checks passed: sample/live states, upload, polling, review, PDF bbox, SHA-256 failure, mobile layout",
  );
} finally {
  await browser.close();
}
