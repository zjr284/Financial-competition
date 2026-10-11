"""金融投研智能体后端包（成员 A 负责的模块集合）。

包内模块对应 docs/项目总纲.md 的模块边界：
ingest 登记文件、parse 解析、evidence 证据、logging 事件、
models 模型适配、tasks 七题实现、orchestrator 编排、api HTTP 服务。
"""

from __future__ import annotations

API_VERSION = "1.0.0"
CODE_VERSION = "member-a-0.1.0"

__all__ = ["API_VERSION", "CODE_VERSION"]
