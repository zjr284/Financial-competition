"""C 插件接入 A 服务与 B 的已复核事实、计算器；不改公共契约。"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from app.errors import AppError
from app.facts.reviewed_store import ReviewedFactPort
from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.research_verification.support import digest
from app.tasks.report_quality import ReportQualityTask
from app.tools import FinancialCalculator
from contracts.ports import TaskContext


class DocumentBridge:
    def __init__(self, documents, parse):
        self.documents, self.parse = documents, parse

    def get_document(self, document_id):
        return self.documents.get_document(document_id)

    def parse_document(self, document_id):
        return self.parse.parse_document(document_id).to_dict()


class EventBridge:
    """同时对接持久化日志和 A 的重放事件收集器，保留调用哈希。"""
    def __init__(self, events):
        self.events = events

    def append_event(self, run_id, event):
        self.events.append(
            run_id, event_type=event["event_type"], message=event["message"],
            tool_name=event.get("tool_name"), input_hash=event.get("input_hash"),
            output_hash=event.get("output_hash"),
        )


class RuntimeTask:
    def __init__(self, plugin, manifest_path):
        self.plugin, self.manifest_path = plugin, Path(manifest_path)
        self.task_type = plugin.task_type

    def _snapshot(self, context):
        # 首次执行固定本 Run 的事实；后续复核清单更新不能改变离线重放。
        path = context.settings.runs_dir / context.run_id / "c-reviewed-facts.json"
        if not path.exists():
            payload = (json.loads(self.manifest_path.read_text(encoding="utf-8"))
                       if self.manifest_path.is_file() else {"version": 1, "documents": []})
            if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("documents"), list):
                raise ValueError("复核事实清单必须包含 version=1 和 documents 数组")
            documents = payload["documents"]
            if any(not isinstance(bundle, dict) for bundle in documents):
                raise ValueError("复核事实文档项必须是对象")
            payload = {"version": 1, "documents": [bundle for bundle in documents
                       if bundle.get("document_id") in context.document_ids]}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        fingerprint = digest(json.loads(path.read_text(encoding="utf-8")))
        context.events.append(
            context.run_id, event_type="tool_call", tool_name="c.reviewed_fact_snapshot",
            message="读取本次运行固定的复核事实快照", input_hash=fingerprint, output_hash=fingerprint,
        )
        return path

    def run(self, context, task_input):
        try:
            snapshot = self._snapshot(context)
            documents = DocumentBridge(context.documents, context.parse)
            bridged = TaskContext(
                run_id=context.run_id, document_ids=tuple(context.document_ids),
                documents=documents, evidence=ReviewedFactPort(snapshot, documents, context.parse),
                calculations=FinancialCalculator(), model=context.model, events=EventBridge(context.events),
            )
            return self.plugin.run(bridged, task_input)
        except AppError:
            raise
        except Exception as error:
            code = "VALIDATION_ERROR" if isinstance(error, (ValueError, KeyError, TypeError, jsonschema.ValidationError)) else "INTERNAL_ERROR"
            raise AppError(code, "角色 C 任务输入、证据或事实清单未通过处理",
                           details={"task_type": self.task_type, "error_type": type(error).__name__}) from error


def install_role_c(app, manifest_path=None):
    """启动前显式注册 C 两题；不自动修改 A 的默认装配。"""
    path = Path(manifest_path) if manifest_path is not None else app.settings.data_dir / "reviewed_facts.json"
    app.orchestrator.task_registry.update({
        plugin.task_type: RuntimeTask(plugin, path)
        for plugin in (ResearchVerificationTask(), ReportQualityTask())
    })
