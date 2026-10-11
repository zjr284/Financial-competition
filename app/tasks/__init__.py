"""tasks：七题任务插件注册表。

成员 A 负责 `structured_extraction`；其余六题由 B、C 提供实现后在此注册。
未实现的选题不会返回伪造数据，而是给出 MISSING_INPUT / 明确的 MODEL_UNAVAILABLE 提示，
保证“七题通过同一 API 启动”的接口形状始终成立。
"""

from __future__ import annotations

from typing import Any


TASK_TYPES = (
    "structured_extraction",
    "financial_analysis",
    "industry_impact",
    "valuation",
    "research_verification",
    "investment_memo",
    "report_quality",
)

# 已交付的选题 -> 所属成员
IMPLEMENTED = {
    "structured_extraction": "A",
}


def build_task(task_type: str, *, parse: Any = None, settings: Any = None, registry: dict[str, Any] | None = None) -> Any:
    """构造任务插件。registry 允许测试注入桩实现（联调其它成员模块）。"""
    if registry and task_type in registry:
        return registry[task_type]
    if task_type == "structured_extraction":
        from app.tasks.structured_extraction import StructuredExtractionTask

        return StructuredExtractionTask(parse, settings)
    return NotImplementedTask(task_type)


class NotImplementedTask:
    """未接入的选题：抛出结构化错误，绝不返回编造结果。"""

    def __init__(self, task_type: str) -> None:
        self.task_type = task_type

    def run(self, context: Any, task_input: dict[str, Any]) -> dict[str, Any]:
        from app.errors import AppError

        owner = {"financial_analysis": "B", "industry_impact": "B", "valuation": "B", "investment_memo": "B", "research_verification": "C", "report_quality": "C"}.get(
            self.task_type, "?"
        )
        raise AppError(
            "MISSING_INPUT",
            f"任务 {self.task_type} 尚未接入（主责成员 {owner}）；接口形状已就绪，可用 contracts/examples 联调",
            details={"task_type": self.task_type, "owner": owner},
        )


__all__ = ["TASK_TYPES", "IMPLEMENTED", "build_task", "NotImplementedTask"]
