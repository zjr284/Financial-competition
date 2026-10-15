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
        if schema_name in {"IndustryData", "MemoData"}:
            raise AppError("MODEL_UNAVAILABLE", "离线规则后端不生成语义分析；请显式配置模型服务")
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
    """Configured chat-completions JSON transport; credentials never enter records."""

    def __init__(self, settings: Settings, *, endpoint_env: str = "FINSCOPE_MODEL_ENDPOINT", key_env: str = "FINSCOPE_MODEL_KEY") -> None:
        import os

        self.settings = settings
        self.endpoint = os.environ.get(endpoint_env)
        self.api_key = os.environ.get(key_env)
        self.calls: list[ModelCallRecord] = []
        self.last_success = False
        self.timeout = float(os.environ.get("FINSCOPE_MODEL_TIMEOUT_SECONDS", "60"))
        self.max_attempts = int(os.environ.get("FINSCOPE_MODEL_MAX_ATTEMPTS", "2"))
        if not 0 < self.timeout <= 300 or not 1 <= self.max_attempts <= 3:
            raise ValueError("Invalid model timeout or attempt limit")
        if self.endpoint:
            from urllib.parse import urlsplit
            url = urlsplit(self.endpoint)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("Model endpoint must be a credential-free HTTP(S) URL")
            if not url.path.rstrip("/").endswith("/chat/completions"):
                raise ValueError("Model endpoint must include the full /chat/completions path")

    @property
    def available(self) -> bool:
        return bool(self.endpoint and self.settings.model_id != "rule-based-offline")

    def health(self) -> dict[str, Any]:
        return {"backend": "remote-json", "model_id": self.settings.model_id,
                "model_revision": self.settings.model_revision, "prompt_version": self.settings.prompt_version,
                "configured": self.available, "available": self.last_success,
                "reason": "最近调用已通过协议与结构校验" if self.last_success else "尚无成功调用；不以配置存在代表服务连通"}

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
                "未配置完整模型端点和模型名，无法执行模型推理",
                details={"task_code": task_code, "schema_name": schema_name},
            )
        from app.models.remote import request_json
        import time

        started = time.perf_counter()
        self.last_success = False
        output, attempts = request_json(self, task_code, evidence_ids, schema_name, context)
        self.calls.append(ModelCallRecord(
            model_id=self.settings.model_id, model_revision=self.settings.model_revision,
            prompt_version=self.settings.prompt_version, task_code=task_code,
            schema_name=schema_name, evidence_ids=list(evidence_ids),
            input_hash=model_input_hash(evidence_ids, context), output_hash=stable_hash(output),
            called_at=now_iso(), latency_ms=int((time.perf_counter() - started) * 1000), attempts=attempts,
        ))
        self.last_success = True
        return output


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
