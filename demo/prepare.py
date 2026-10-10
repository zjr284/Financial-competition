"""生成离线 Web 数据，不启动服务器。"""
import json
from pathlib import Path

from demo.adapters import load_package
from demo.run_demo import run


def prepare():
    root = Path(__file__).resolve().parents[1]
    results, events, manifest = run(root / "demo" / "output")
    (root / "web" / "demo.json").write_text(json.dumps({"package": load_package(), "results": results,
                                                      "events": events, "manifest": manifest}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    prepare()
