"""C 浏览器联调专用本机 JSON 响应器，不调用云端或模拟真实模型效果。"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading

from api.server import ApiServer
from app.config import Settings
from app.container import build_app
from app.facts.review_workflow import init_manifest
from app.facts.semantic_sources import register_excerpt
from app.tasks.a_bridge import install_role_b
from demo.prepare import prepare
from demo.serve import Handler as WebHandler
from demo.synthesis_fixtures import SOURCE, protocol_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.exists() and any(args.data_dir.iterdir()):
        parser.error("使用新的独立目录，不能覆写现有资料")
    requests = []
    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if self.path == "/fixture-stop":
                raw = json.dumps({"protocol_requests": len(requests)}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
                threading.Thread(target=model_server.shutdown, daemon=True).start()
                return
            if self.path != "/v1/chat/completions": self.send_error(404); return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024: raise ValueError("limit")
                body = json.loads(self.rfile.read(size))
                user = json.loads(body["messages"][-1]["content"])
                output = protocol_output(user["schema_name"], user["evidence_ids"], user["context"])
                requests.append(user["task_code"])
                raw = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output, ensure_ascii=False)}}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            except (ValueError, KeyError, TypeError):
                self.send_error(400, "Invalid local protocol fixture request")
    model_server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=model_server.serve_forever, daemon=True); thread.start()
    # 只改变这个测试子进程的环境；覆盖继承的真实云端设置，绝不读取或展示真实密钥。
    os.environ.update(FINSCOPE_MODEL_BACKEND="remote-json", FINSCOPE_MODEL_ENDPOINT=f"http://127.0.0.1:{model_server.server_port}/v1/chat/completions",
                      FINSCOPE_MODEL_KEY="", FINSCOPE_MODEL_TIMEOUT_SECONDS="5", FINSCOPE_MODEL_MAX_ATTEMPTS="1")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE.parents[2], text=True).strip()
    app = None; api = None; web = None
    try:
        app = build_app(Settings(data_dir=args.data_dir.resolve(), model_id="c-local-protocol-fixture", model_revision="not-a-real-model", code_version=revision))
        document = app.upload_path(str(SOURCE), disclosure_date="2026-08-01")
        eids = app.parse_document(document["document_id"])["evidence_ids"]
        eid = eids[1]
        manifest = app.settings.data_dir / "reviewed_facts.json"
        init_manifest(app, manifest, [document["document_id"]])
        for source_id in eids: register_excerpt(app, manifest, document["document_id"], source_id)
        install_role_b(app, manifest)
        prepare()
        api = ApiServer(app, port=0).start(background=True)
        web = ThreadingHTTPServer(("127.0.0.1", 0), WebHandler)
        print("SYNTHESIS_FIXTURE_READY " + json.dumps({"api": api.base_url, "web": f"http://127.0.0.1:{web.server_port}",
              "model_fixture": f"http://127.0.0.1:{model_server.server_port}", "document_id": document["document_id"], "evidence_id": eid,
              "synthetic": True, "real_model": False, "git_commit": revision}), flush=True)
        web.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if web is not None: web.server_close()
        if api is not None: api.stop()
        if app is not None: app.close()
        model_server.shutdown(); model_server.server_close(); thread.join()


if __name__ == "__main__": main()
