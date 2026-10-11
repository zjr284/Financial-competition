// 测试/录屏专用：从固定本地文件响应页面请求，适用于禁止 loopback 的执行环境。
import fs from "node:fs/promises";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "..");
const fixture = path.resolve(root, "../demo/fixtures");
const types = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".txt": "text/plain",
};
export async function localSite(context) {
  await context.route("http://127.0.0.1:8765/**", async (route) => {
    const url = new URL(route.request().url());
    const name = decodeURIComponent(url.pathname);
    const target = name.startsWith("/fixtures/")
      ? path.resolve(fixture, name.slice("/fixtures/".length))
      : path.resolve(root, name === "/" ? "index.html" : name.slice(1));
    const base = name.startsWith("/fixtures/") ? fixture : root;
    if (!target.startsWith(base + path.sep)) {
      await route.fulfill({ status: 404, body: "Not found" });
      return;
    }
    try {
      await route.fulfill({
        status: 200,
        contentType: types[path.extname(target)] || "application/octet-stream",
        body: await fs.readFile(target),
      });
    } catch {
      await route.fulfill({ status: 404, body: "Not found" });
    }
  });
}
