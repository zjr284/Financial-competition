import fs from "node:fs/promises";
import { chromium } from "playwright";
import { localSite } from "./local-site.mjs";

// 录制真实 C 规则输出与操作。保留常驻样例标签，不演示未实现的 A/B 功能。
const output = "../demo/output";
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: output, size: { width: 1440, height: 1000 } },
});
await localSite(context);
const page = await context.newPage();
await page.goto("http://127.0.0.1:8765");
await page.locator(".card").first().waitFor();
const caption = async (text) =>
  page.evaluate((text) => {
    let el = document.getElementById("record-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "record-caption";
      Object.assign(el.style, {
        position: "fixed",
        bottom: "20px",
        left: "5%",
        right: "5%",
        zIndex: 10000,
        padding: "16px",
        background: "#102f3bf0",
        color: "white",
        textAlign: "center",
        fontSize: "19px",
        borderRadius: "8px",
      });
      document.body.append(el);
    }
    el.textContent = text;
  }, text);
await caption("成员 C 模块真实运行：合成资料测试，未接入真实披露与模型。");
await page.waitForTimeout(5000);
await page.getByRole("button", { name: "原句定位" }).first().click();
await page.locator("#bbox").waitFor({ state: "visible" });
await caption(
  "正确声明、数值错误与待复核声明分别呈现；点击原句查看页码和坐标。",
);
await page.waitForTimeout(6000);
await page.getByRole("button", { name: "来源 1", exact: true }).first().click();
await caption("来源与输入文件哈希固定；此页面是合成 TXT 坐标示意。");
await page.waitForTimeout(5000);
await page.locator(".card.incorrect").first().scrollIntoViewIfNeeded();
await caption("明确数值错误保留原句、原始证据与建议改写。");
await page.waitForTimeout(5000);
await page.getByText("计算轨迹", { exact: true }).click();
await page.locator("#calculations").scrollIntoViewIfNeeded();
await caption("同比由 CalculationPort 重算；记录输入 Fact ID、公式和计算 ID。");
await page.waitForTimeout(6000);
await page.locator("#sample-result").selectOption("1");
await page.locator("#status").filter({ hasText: "已完成" }).waitFor();
await page.evaluate(() => scrollTo(0, 0));
await caption(
  "成功样例：两条数值声明核验通过。合成冒烟结果不代表竞赛实测精确率。",
);
await page.waitForTimeout(5000);
await page.locator("#sample-result").selectOption("2");
await page.getByText("规则暂定评分 45/100", { exact: true }).waitFor();
await caption("报告质量评分展示公开扣分规则；最终质量判断保留人工复核。");
await page.waitForTimeout(6000);
await page.getByRole("button", { name: "连接团队 API" }).click();
await page.evaluate(() => scrollTo(0, 0));
await caption(
  "A 提供服务后可上传文件、轮询七题与提交复核；本次未宣称全系统完成。",
);
await page.waitForTimeout(5000);
const video = page.video();
await context.close();
await video.saveAs(output + "/c-demo.webm");
await browser.close();
console.log("Recorded C module demonstration: demo/output/c-demo.webm");
