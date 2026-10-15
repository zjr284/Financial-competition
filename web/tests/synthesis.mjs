import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { chromium } from "playwright";
import { synthesisView, synthesisCSV, synthesisHTML } from "../synthesis.js";

const data = JSON.parse(await fs.readFile("demo.json", "utf8"));
for (const result of data.results.slice(5, 7)) {
  const view = synthesisView(result, true);
  assert.ok(synthesisCSV(view).includes("全部虚构"));
  assert.ok(synthesisHTML(view).includes("待人工"));
}
assert.ok(
  synthesisCSV(synthesisView(data.results[7], true)).includes(
    data.results[7].review_items[0].reason,
  ),
);
const bad = structuredClone(data.results[6]);
bad.data.thesis[0].evidence_ids = ["ev_missing"];
bad.data.thesis[0].text = '<script>alert("bad")</script>';
assert.equal(synthesisView(bad).groups[0].items[0].usableRefs.length, 0);
assert.ok(
  synthesisView(bad).issues.some((item) => item.includes("未在结果中登记")),
);
assert.ok(!synthesisHTML(synthesisView(bad)).includes("<script>"));
bad.data.thesis[0].text = '=HYPERLINK("bad")';
assert.ok(synthesisCSV(synthesisView(bad)).includes("'=HYPERLINK"));
const record = process.argv.includes("--record");
const revision = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: "..",
  encoding: "utf8",
  windowsHide: true,
}).trim();
if (
  record &&
  execFileSync("git", ["status", "--porcelain"], {
    cwd: "..",
    encoding: "utf8",
    windowsHide: true,
  }).trim()
)
  throw new Error("先commit，再录制与干净提交一致的视频");
const output = path.resolve("../demo/output");
await fs.mkdir(output, { recursive: true });
const directory = await fs.mkdtemp(
  path.join(os.tmpdir(), "c-synthesis-browser-"),
);
const service = spawn(
  process.env.PYTHON || "python",
  ["-m", "demo.synthesis_live", "--data-dir", directory],
  {
    cwd: "..",
    windowsHide: true,
    env: { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
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
  while (!stdout.includes("SYNTHESIS_FIXTURE_READY ")) {
    if (service.exitCode !== null || Date.now() > deadline)
      throw new Error("本机协议测试服务启动失败：" + stderr);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const ready = JSON.parse(
    stdout
      .split("\n")
      .find((line) => line.startsWith("SYNTHESIS_FIXTURE_READY "))
      .slice("SYNTHESIS_FIXTURE_READY ".length),
  );
  assert.equal(ready.real_model, false);
  assert.equal(ready.git_commit, revision);
  browser = await chromium.launch({
    executablePath:
      process.env.CHROME_PATH ||
      "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: true,
  });
  const context = await browser.newContext({
    acceptDownloads: true,
    viewport: { width: 1440, height: 1000 },
    ...(record
      ? { recordVideo: { dir: output, size: { width: 1440, height: 1000 } } }
      : {}),
  });
  const page = await context.newPage(),
    errors = [],
    results = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(ready.web);
  await page.locator(".card").first().waitFor();
  await page.evaluate(() => {
    const banner = document.createElement("div");
    banner.textContent =
      "C 语义界面联调 · 全部虚构 · 本机协议响应器 · 不是大模型效果或真人金标";
    Object.assign(banner.style, {
      position: "fixed",
      bottom: "10px",
      left: "5%",
      right: "5%",
      zIndex: 10000,
      padding: "14px",
      color: "white",
      background: "#102f3b",
      textAlign: "center",
    });
    document.body.append(banner);
  });
  const pause = async () => {
    if (record) await page.waitForTimeout(4500);
  };
  // 页面样例和缺来源状态也由实际静态服务提供，没有 route 拦截。
  await page.locator("#sample-result").selectOption("6");
  await page.locator('.semantic-section[data-section="risks"]').waitFor();
  assert.match(
    await page.locator("#results").textContent(),
    /事实性陈述（待核实）/,
  );
  await pause();
  await page.locator("#sample-result").selectOption("7");
  await page
    .getByText("暂无内容，须检查来源与任务复核项。", { exact: true })
    .first()
    .waitFor();
  await page.getByRole("button", { name: "连接团队 API" }).click();
  await page.locator("#api-base").fill(ready.api);
  await page.locator("#document-ids").fill(ready.document_id);
  for (const task of ["industry_impact", "investment_memo"]) {
    await page.locator("#task-type").selectOption(task);
    const taskInput =
      task === "industry_impact"
        ? {
            focus_company_code: "999999",
            event_description: "虚构的订单变化",
            scenario_ids: ["base", "reverse"],
          }
        : { company_code: "999999", as_of_date: "2026-09-30" };
    await page.locator("#task-input").fill(JSON.stringify(taskInput));
    await page.locator("#run").click();
    await page.waitForFunction(
      () => !document.getElementById("export").disabled,
    );
    const result = JSON.parse(await page.locator("#raw").textContent());
    assert.equal(result.status, "needs_review");
    assert.match(
      await page.locator("#mode-badge").textContent(),
      /实际运行结果/,
    );
    assert.equal(result.task_type, task);
    results.push(result);
    await page.locator(".semantic-card").first().scrollIntoViewIfNeeded();
    await pause();
    await page
      .getByRole("button", { name: "查看依据 1", exact: true })
      .first()
      .click();
    await page
      .locator("#location-note")
      .filter({ hasText: "非 PDF" })
      .waitFor();
    assert.match(
      await page.locator("#evidence-meta").textContent(),
      /SHA-256 [a-f0-9]{64}/,
    );
    assert.match(
      await page.locator("#evidence-text").textContent(),
      /测试客户/,
    );
    await pause();
    await page.screenshot({
      path: path.join(output, `c-${task}.png`),
      fullPage: true,
    });
    for (const format of ["html", "csv"]) {
      const downloadPromise = page.waitForEvent("download");
      await page.locator("#export-synthesis-" + format).click();
      const download = await downloadPromise,
        target = path.join(output, `c-${task}.${format}`);
      await download.saveAs(target);
      const exported = await fs.readFile(target, "utf8");
      assert.match(exported, /待人工审核/);
      assert.ok(exported.includes(ready.evidence_id));
      assert.ok(exported.includes(result.review_items[0].reason));
    }
    const review = page.locator(".review-card").first();
    await review.getByLabel("复核人").fill("automated-protocol-test");
    await review.getByLabel("复核依据").fill("仅自动协议测试，不是真人审核");
    await review.getByRole("button", { name: "提交人工决定" }).click();
    await page
      .locator("#status")
      .filter({ hasText: "人工决定已保存" })
      .waitFor();
    await pause();
  }
  const stop = await fetch(ready.model_fixture + "/fixture-stop", {
    method: "POST",
  });
  assert.equal((await stop.json()).protocol_requests, 2);
  for (const result of results) {
    const replay = await fetch(`${ready.api}/runs/${result.run_id}/replay`, {
      method: "POST",
    });
    assert.equal(replay.ok, true);
    const proof = await replay.json();
    for (const key of [
      "input_hash_match",
      "result_hash_match",
      "trace_match",
      "result_original_matches",
    ])
      assert.equal(proof[key], true, key);
    assert.equal(proof.chain.ok, true);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  assert.deepEqual(errors, []);
  const video = page.video();
  await context.close();
  if (video) await video.saveAs(path.join(output, "c-synthesis.webm"));
  await fs.writeFile(
    path.join(output, "c-synthesis-results.json"),
    JSON.stringify(
      { synthetic: true, real_model: false, git_commit: revision, results },
      null,
      2,
    ),
  );
  console.log(
    "Synthesis actual HTTP browser checks passed: sections, source hash, exports, review submission, mobile and offline replay after stopping protocol responder",
  );
} finally {
  if (browser) await browser.close();
  service.kill();
  await stopped;
  if (
    path.dirname(path.resolve(directory)) !== path.resolve(os.tmpdir()) ||
    !path.basename(directory).startsWith("c-synthesis-browser-")
  )
    throw new Error("临时路径不在预期范围内");
  await fs.rm(directory, { recursive: true, force: true });
}
