import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { chromium } from "playwright";

// 不拦截页面/API 请求：启动实际 A/B/C 服务并走浏览器操作。
const recording = process.argv.includes("--record");
const revision = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: "..",
  encoding: "utf8",
  windowsHide: true,
}).trim();
const dirty = Boolean(
  execFileSync("git", ["status", "--porcelain"], {
    cwd: "..",
    encoding: "utf8",
    windowsHide: true,
  }).trim(),
);
if (recording && dirty)
  throw new Error("请先 commit，再录制与干净提交一致的演示视频");
const output = path.resolve("../demo/output");
await fs.mkdir(output, { recursive: true });
const directory = await fs.mkdtemp(path.join(os.tmpdir(), "c-live-browser-"));
const service = spawn(
  process.env.PYTHON || "python",
  [
    "-m",
    "demo.live",
    "--synthetic",
    "--data-dir",
    directory,
    "--api-port",
    "0",
    "--web-port",
    "0",
  ],
  {
    cwd: "..",
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      FINSCOPE_MODEL_BACKEND: "offline-rule",
      FINSCOPE_MODEL_ENDPOINT: "",
      FINSCOPE_MODEL_KEY: "",
      FINSCOPE_EXTRACTION_BACKEND: "rules",
    },
  },
);
let stdout = "",
  stderr = "",
  browser;
service.stdout.on("data", (chunk) => {
  stdout += chunk.toString();
});
service.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});
const stopped = new Promise((resolve) => service.on("exit", resolve));
try {
  const deadline = Date.now() + 60000;
  while (!stdout.includes("（选择连接团队 API）")) {
    if (service.exitCode !== null || Date.now() > deadline)
      throw new Error("实际服务启动失败：" + stderr);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const seeded = JSON.parse(
    stdout
      .split("\n")
      .find((line) => line.includes("非真实竞赛评测："))
      .split("非真实竞赛评测：")[1],
  );
  const api = stdout.match(/API：(http:\/\/[^；]+)/)[1];
  const site = stdout.match(/页面：(http:\/\/[^（]+)/)[1];
  browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ||
      "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    ...(recording
      ? { recordVideo: { dir: output, size: { width: 1440, height: 1000 } } }
      : {}),
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(site);
  await page.locator(".card").first().waitFor();
  await page.evaluate(() => {
    const banner = document.createElement("div");
    banner.textContent =
      "C 实际 HTTP 联调 · 合成 PDF/事实 · 非竞赛实测 · 未接入大模型";
    Object.assign(banner.style, {
      position: "fixed",
      bottom: "12px",
      left: "10%",
      right: "10%",
      background: "#102f3b",
      color: "white",
      padding: "14px",
      textAlign: "center",
      zIndex: 10000,
    });
    document.body.append(banner);
  });
  const pause = async () => {
    if (recording) await page.waitForTimeout(4500);
  };
  await page.getByRole("button", { name: "连接团队 API" }).click();
  await page.locator("#api-base").fill(api);
  await page.locator("#health").click();
  await page.getByText("团队 API 已连接", { exact: true }).waitFor();
  // 从真实登记快照取得原文件，再通过页面上传验证内容寻址去重。
  const source = await fetch(
    `${api}/documents/${seeded.documents.source}/content`,
  );
  await page.locator("#upload").setInputFiles({
    name: "source.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(await source.arrayBuffer()),
  });
  await page.locator("#upload-button").click();
  await page.locator("#status").filter({ hasText: "文件已登记" }).waitFor();
  assert.equal(
    (await page.locator("#document-ids").inputValue()).trim(),
    seeded.documents.source,
  );
  await pause();
  const results = [];
  for (const report of ["success", "draft"]) {
    await page
      .locator("#document-ids")
      .fill(`${seeded.documents.source}\n${seeded.documents[report]}`);
    await page.locator("#task-input").fill(
      JSON.stringify({
        draft_document_id: seeded.documents[report],
        company_code: "999999",
      }),
    );
    await page.locator("#run").click();
    await page.waitForFunction(
      () => !document.getElementById("export").disabled,
    );
    const result = JSON.parse(await page.locator("#raw").textContent());
    assert.deepEqual(result.data.summary, {
      correct: 2,
      incorrect: report === "success" ? 0 : 2,
      needs_review: report === "success" ? 0 : 5,
    });
    assert.equal(result.calculations[0].value, "20");
    results.push(result);
    await pause();
    await page
      .getByRole("button", { name: "来源 1", exact: true })
      .first()
      .click();
    await page
      .locator("#location-note")
      .filter({ hasText: "固定 PDF 原件已校验哈希" })
      .waitFor();
    assert.ok(await page.locator("#bbox").isVisible());
    assert.ok(await page.locator("#pdf-canvas").isVisible());
    await pause();
    if (report === "draft") {
      await page.screenshot({
        path: path.join(output, "c-live-pdf.png"),
        fullPage: true,
      });
      const review = page.locator(".review-card").first();
      assert.equal(await review.getByLabel("修正值").isVisible(), false);
      await review.getByLabel("复核人").fill("automated-synthetic-test");
      await review
        .getByLabel("复核依据")
        .fill("合成自动测试操作，不代表真人标注或金融签字");
      await review.getByRole("button", { name: "提交人工决定" }).click();
      await page
        .locator("#status")
        .filter({ hasText: "人工决定已保存" })
        .waitFor();
      await pause();
    }
  }
  await page.locator("#task-type").selectOption("report_quality");
  await page
    .locator("#task-input")
    .fill(JSON.stringify({ report_document_ids: [seeded.documents.draft] }));
  await page.locator("#run").click();
  await page.getByText("规则暂定评分 45/100", { exact: true }).waitFor();
  results.push(JSON.parse(await page.locator("#raw").textContent()));
  await pause();
  // 两项重点题继续走实际 A/B/C HTTP；合成 PDF 和事实不代表真实金融验收。
  for (const task of ["financial_analysis", "structured_extraction"]) {
    await page.locator("#task-type").selectOption(task);
    await page
      .locator("#document-ids")
      .fill(
        task === "financial_analysis"
          ? seeded.documents.source
          : seeded.documents.announcement,
      );
    await page.locator("#task-input").fill(
      JSON.stringify(
        task === "financial_analysis"
          ? {
              company_code: "999999",
              periods: ["2025-12-31", "2024-12-31"],
              statement_scope: "consolidated",
            }
          : { announcement_types: ["pledge"] },
      ),
    );
    await page.locator("#run").click();
    await page.waitForFunction(
      () => !document.getElementById("export").disabled,
    );
    const result = JSON.parse(await page.locator("#raw").textContent());
    assert.equal(result.task_type, task);
    results.push(result);
    if (task === "financial_analysis") {
      assert.equal(
        result.calculations.find((calc) => calc.formula_code === "yoy").value,
        "20",
      );
      assert.equal(
        await page.locator(".financial-fact").count(),
        result.data.facts.length,
      );
      assert.match(
        await page.locator(".financial-calculation").first().textContent(),
        /20/,
      );
    } else {
      const field = page
        .locator('.field-card[data-field="pledged_shares"]')
        .first();
      await field.waitFor();
      assert.match(await field.textContent(), /12500000/);
      assert.match(
        await page
          .locator('.field-card[data-field="pledge_end_date"]')
          .first()
          .textContent(),
        /未确定/,
      );
      await field.getByRole("button").first().click();
      await page
        .locator("#location-note")
        .filter({ hasText: "固定 PDF 原件已校验哈希" })
        .waitFor();
      assert.match(
        await page.locator("#evidence-text").textContent(),
        /1,250万股/,
      );
      assert.ok(await page.locator("#bbox").isVisible());
      await page.screenshot({
        path: path.join(output, "c-live-extraction.png"),
        fullPage: true,
      });
    }
    for (const format of ["html", "csv"]) {
      const pending = page.waitForEvent("download");
      await page.locator("#export-disclosure-" + format).click();
      const download = await pending;
      const raw = await fs.readFile(await download.path(), "utf8");
      assert.ok(raw.includes(result.run_id));
      assert.ok(!download.suggestedFilename().startsWith("synthetic-"));
      // API 不声明资料真假，导出不能仅因真实 HTTP 就声称 real/synthetic=false。
      if (format === "csv") assert.ok(raw.includes("not_declared_by_api"));
    }
    const stored = await fetch(`${api}/runs/${result.run_id}/result`);
    assert.equal(stored.status, 200);
    assert.deepEqual(await stored.json(), result);
    await pause();
  }
  assert.deepEqual(errors, []);
  const video = page.video();
  await context.close();
  if (video) await video.saveAs(path.join(output, "c-live.webm"));
  await fs.writeFile(
    path.join(output, "c-live-results.json"),
    JSON.stringify(
      {
        synthetic: true,
        git_commit: revision,
        working_tree_dirty: dirty,
        results,
      },
      null,
      2,
    ),
  );
  console.log(
    "Actual HTTP browser checks passed: upload, C success/mixed/quality, extraction fields/PDF, financial facts/calculations, HTML/CSV and review",
  );
} finally {
  if (browser) await browser.close();
  service.kill();
  await stopped;
  if (
    path.dirname(path.resolve(directory)) !== path.resolve(os.tmpdir()) ||
    !path.basename(directory).startsWith("c-live-browser-")
  ) {
    throw new Error("测试临时目录不在预期范围内");
  }
  await fs.rm(directory, { recursive: true, force: true });
}
