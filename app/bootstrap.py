"""可选的本地依赖引导。

评审环境可能没有 pypdf / pdfminer.six。项目把可选依赖安装到 `vendor/python`，
本模块把它加入 sys.path，使 PDF 解析在该环境下仍然可用；缺失时解析层会
自动退回纯标准库后端，不会报错中断。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor" / "python"


def bootstrap() -> list[str]:
    """把 vendor 目录加入 sys.path，返回当前可用的可选库列表。"""
    available: list[str] = []
    if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
        sys.path.append(str(VENDOR_DIR))
    for module in ("pdfminer", "pypdf", "openpyxl"):
        try:
            __import__(module)
            available.append(module)
        except Exception:
            continue
    return available


if __name__ == "__main__":  # pragma: no cover
    print("vendor dir:", VENDOR_DIR, "exists:", VENDOR_DIR.exists())
    print("available:", bootstrap())
