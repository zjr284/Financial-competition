"""本地静态演示服务器；不实现或替代 A 的 /api/v1。"""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from demo.adapters import load_package
from demo.prepare import prepare
from demo.ttm_display import fixture_paths

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def do_GET(self):
        if self.path.startswith("/fixtures/"):
            name = self.path.removeprefix("/fixtures/")
            allowed = {d["filename"]: ROOT / "demo" / "fixtures" / d["filename"] for d in load_package()["documents"]}
            allowed.update(fixture_paths())
            allowed["synthesis.txt"] = ROOT / "demo" / "fixtures" / "synthesis.txt"
            allowed["announcement.txt"] = ROOT / "demo" / "fixtures" / "announcement.txt"
            if name not in allowed:
                self.send_error(404)
                return
            raw = allowed[name].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            super().do_GET()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    prepare()
    print(f"C 合成演示：http://127.0.0.1:{args.port}（不是实际团队 API）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
