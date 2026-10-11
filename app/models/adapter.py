"""模型适配层（contracts/ports.py ModelPort）。

职责边界（AGENTS.md、docs/接口约定.md）：
  * 模型只做候选抽取、语义解释和组织任务；
  * 模型不得直接生成未经工具重算的财务结果 —— 因此这里的返回结构里没有
    “最终数值”，只有候选字符串（candidate）、原文片段与置信度；
  * infer_json 必须保存模型 ID、修订号、提示词版本、输入证据 ID 与输出哈希。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.common.contracts import validate_contract
from app.common.ids import digest_of, now_iso
from app.config import Settings
from app.errors import AppError


@dataclass
class ModelCallRecord:
    """一次模型调用的可追溯记录，写入 Event 与运行清单。"""

    model_id: str
    model_revision: str
    prompt_version: str
    task_code: str
    schema_name: str
    evidence_ids: list[str]
    input_hash: str
    output_hash: str
    called_at: str
    latency_ms: int = 0
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "prompt_version": self.prompt_version,
            "task_code": self.task_code,
            "schema_name": self.schema_name,
            "evidence_ids": list(self.evidence_ids),
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "called_at": self.called_at,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
        }


@dataclass
class ModelResult:
    """infer_json 的返回：候选 JSON + 调用记录。"""

    output: dict[str, Any]
    record: ModelCallRecord


class ModelAdapter(Protocol):
    def infer_json(
        self,
        task_code: str,
        evidence_ids: list[str],
        schema_name: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class OfflineRuleModel:
    """离线确定性候选生成器。

    在没有可用大模型权重的环境（评审离线运行）下，用规则给出候选片段；
    所有数值仍需 app/tasks 的确定性归一化写入，模型不产出最终数值。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls: list[ModelCallRecord] = []

    def infer_json(
        self,
        task_code: str,
        evidence_ids: list[str],
        schema_name: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.infer(task_code, evidence_ids, schema_name, context)
        return result.output

    def infer(
        self,
        task_code: str,
        evidence_ids: list[str],
        schema_name: str,
        context: dict[str, Any],
    ) -> ModelResult:
        import time

        started = time.perf_counter()
        payload = {
            "task_code": task_code,
            "schema_name": schema_name,
            "evidence_ids": evidence_ids,
            "context": context,
        }
        # 离线后端不使用任何外部模型：输出由 context 中的候选片段透传，
        # 只做结构包装，绝不生成新数值。
        candidates = context.get("candidates", [])
        output: dict[str, Any] = {
            "backend": "offline-rule",
            "schema_name": schema_name,
            "candidates": [
                {
                    "field_code": str(item.get("field_code", "")),
                    "candidate_text": str(item.get("candidate_text", "")),
                    "evidence_ids": list(item.get("evidence_ids", [])),
                }
                for item in candidates
                if isinstance(item, dict)
            ],
            "notes": "离线规则后端：仅给出候选原文片段，数值由确定性工具计算",
        }
        record = ModelCallRecord(
            model_id=self.settings.model_id,
            model_revision=self.settings.model_revision,
            prompt_version=self.settings.prompt_version,
            task_code=task_code,
            schema_name=schema_name,
            evidence_ids=list(evidence_ids),
            input_hash=digest_of(payload),
            output_hash=digest_of(output),
            called_at=now_iso(),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=1,
        )
        self.calls.append(record)
        return ModelResult(output=output, record=record)

    def health(self) -> dict[str, Any]:
        return {
            "backend": "offline-rule",
            "model_id": self.settings.model_id,
            "model_revision": self.settings.model_revision,
            "prompt_version": self.settings.prompt_version,
            "available": True,
            "reason": "无需外部模型的确定性规则后端",
        }


class RemoteJsonModel:
    """远程 JSON 模型适配器骨架。

    交付说明里要求“不能提供的第三方模型权重只提供调用说明与版本信息”，
    因此这里只定义接口与校验，不内置任何密钥；密钥从环境变量读取，
    未配置时按 MODEL_UNAVAILABLE 失败并把原因写进 Run.error。
    """

    def __init__(self, settings: Settings, *, endpoint_env: str = "FINSCOPE_MODEL_ENDPOINT", key_env: str = "FINSCOPE_MODEL_KEY") -> None:
        import os

        self.settings = settings
        self.endpoint = os.environ.get(endpoint_env)
        self.api_key = os.environ.get(key_env)
        self.calls: list[ModelCallRecord] = []

    @property
    def available(self) -> bool:
        return bool(self.endpoint)

    def infer_json(
        self,
        task_code: str,
        evidence_ids: list[str],
        schema_name: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.available:
            raise AppError(
                "MODEL_UNAVAILABLE",
                "未配置远程模型端点，无法执行模型推理",
                details={"task_code": task_code, "schema_name": schema_name},
            )
        raise NotImplementedError("接入具体模型服务时实现 HTTP 调用并复用 ModelCallRecord 记录版本")


def model_input_hash(evidence_ids: list[str], context: dict[str, Any]) -> str:
    return digest_of({"evidence_ids": list(evidence_ids), "context": context})


def validate_model_output(schema_name: str, output: dict[str, Any]) -> None:
    """校验模型输出结构；schema_name 为契约 $defs 名称。"""
    try:
        validate_contract(schema_name, output, label=f"模型输出({schema_name})")
    except Exception as exc:  # 统一包装成模型错误，便于编排层写 needs_review
        raise AppError(
            "MODEL_UNAVAILABLE",
            f"模型输出不符合 {schema_name} 结构：{exc}",
            details={"schema_name": schema_name},
        ) from exc


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
