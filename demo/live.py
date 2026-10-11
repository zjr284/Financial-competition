"""显式装配 A/B/C 本机服务；默认不载入合成事实。"""
import argparse
import json
import subprocess
from http.server import ThreadingHTTPServer
from pathlib import Path

from api.server import ApiServer
from app.config import Settings
from app.container import build_app
from app.tasks.a_bridge import install_role_b
from app.tasks.research_verification.runtime import install_role_c
from demo.prepare import prepare
from demo.serve import Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("var/c-live"))
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--synthetic", action="store_true", help="显式载入虚构公司资料，使用独立的新目录")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=8765)
    args = parser.parse_args()
    if args.synthetic and args.facts:
        parser.error("--synthetic 与 --facts 不可同时使用")
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "c-runtime-unversioned"
    app = build_app(Settings(data_dir=args.data_dir.resolve(), code_version=revision))
    api = None
    web = None
    try:
        if args.synthetic:
            from demo.live_fixtures import seed
            seeded = seed(app)
            print("合成资料 · 非真实竞赛评测：" + json.dumps(seeded, ensure_ascii=False), flush=True)
        install_role_b(app, args.facts)
        install_role_c(app, args.facts)
        prepare()
        api = ApiServer(app, port=args.api_port).start(background=True)
        web = ThreadingHTTPServer(("127.0.0.1", args.web_port), Handler)
        print(f"API：{api.base_url}；页面：http://127.0.0.1:{web.server_port}（选择连接团队 API）", flush=True)
        web.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if web is not None:
            web.server_close()
        if api is not None:
            api.stop()
        app.close()


if __name__ == "__main__":
    main()
