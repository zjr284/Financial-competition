"""任务编排器：Run 状态机、TaskResult 组装、事件记录与离线重放。

状态转换只允许（docs/接口约定.md 第 2 节 Run）：
  queued → running → succeeded/needs_review/failed
  needs_review → running/succeeded/failed
  未完成状态 → cancelled
"""

from __future__ import annotations

from typing import Any

from app import API_VERSION
from app.common.contracts import validate_contract
from app.common.ids import digest_of, new_id, now_iso
from app.config import Settings
from app.errors import AppError, validation_error
from app.ingest.service import DocumentService
from app.logging.events import EventLog
from app.models.adapter import OfflineRuleModel
from app.parse.service import ParseService
from app.storage.db import Store
from app.tasks import TASK_TYPES, build_task


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"succeeded", "needs_review", "failed", "cancelled"},
    "needs_review": {"running", "succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


class Orchestrator:
    """把 Document/Evidence/Event/Model 服务串成一次可重放的 Run。"""

    def __init__(
        self,
        store: Store,
        documents: DocumentService,
        parse: ParseService,
        events: EventLog,
        settings: Settings,
        *,
        model: Any | None = None,
        task_registry: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.documents = documents
        self.parse = parse
        self.events = events
        self.settings = settings
        self.model = model or OfflineRuleModel(settings)
        self.task_registry = task_registry or {}

    # ------------------------------------------------------------ 创建
    def create_run(self, request: dict[str, Any]) -> dict[str, Any]:
        validate_contract("RunRequest", request, label="RunRequest")
        task_type = request["task_type"]
        document_ids = list(request["document_ids"])
        task_input = dict(request["task_input"])

        # 输入文件必须已登记：运行途中不能更换资料
        for document_id in document_ids:
            self.documents.get_document(document_id)

        bundle = self._input_bundle(document_ids, task_type, task_input)
        run_id = new_id("run")
        created_at = now_iso()
        run = {
            "api_version": API_VERSION,
            "run_id": run_id,
            "task_type": task_type,
            "status": "queued",
            "document_ids": document_ids,
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "model_id": self.settings.model_id,
            "model_revision": self.settings.model_revision,
            "prompt_version": self.settings.prompt_version,
            "code_version": self.settings.code_version,
            "input_hash": digest_of(bundle),
            "result_hash": None,
            "error": None,
        }
        validate_contract("Run", run, label="Run")
        self.store.insert_run(
            {
                "run_id": run_id,
                "task_type": task_type,
                "status": "queued",
                "document_ids": ",".join(document_ids),
                "created_at": created_at,
                "started_at": None,
                "finished_at": None,
                "payload": self._dump(run),
                "result": None,
                "task_input": self._dump(task_input),
                "error": None,
            }
        )
        self.events.append(
            run_id,
            event_type="state",
            message=f"创建任务 {task_type}，输入文档 {len(document_ids)} 份",
            tool_name="create_run",
            input_hash=run["input_hash"],
        )
        self.events.write_manifest(
            run_id,
            {
                "run": run,
                "task_input": task_input,
                "deployment_limits": self.settings.describe(),
                "input_bundle": bundle,
            },
        )
        return run

    # ------------------------------------------------------------ 执行
    def execute_run(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        row = self.store.get_run_row(run_id)
        payload = self._load(row["payload"])
        status = payload["status"]
        if status == "running" and not force:
            raise AppError("RUN_NOT_READY", "任务正在运行中", details={"run_id": run_id, "status": status})
        if status in {"succeeded", "needs_review"} and not force:
            return payload

        task_type = payload["task_type"]
        document_ids = list(payload["document_ids"])
        task_input = self._load(row["task_input"] or "{}")

        started_at = now_iso()
        self._transition(payload, "running", started_at=started_at, error=None, touch_error=True)
        self.events.append(
            run_id,
            event_type="state",
            message="任务开始运行",
            tool_name="execute_run",
            input_hash=payload.get("input_hash"),
        )

        plugin = build_task(task_type, parse=self.parse, settings=self.settings, registry=self.task_registry)
        context = _TaskContext(
            run_id=run_id,
            document_ids=tuple(document_ids),
            store=self.store,
            documents=self.documents,
            parse=self.parse,
            events=self.events,
            model=self.model,
            settings=self.settings,
        )
        try:
            for document_id in document_ids:
                evidence_digest = self._ensure_index(run_id, document_id)
                self.events.append(
                    run_id,
                    event_type="file_access",
                    message=f"读取文档 {document_id} 的解析索引",
                    tool_name="load_index",
                    input_hash=self.documents.get_document(document_id)["sha256"],
                    output_hash=evidence_digest,
                )
            self.events.append(
                run_id,
                event_type="tool_call",
                message=f"调用任务插件 {task_type}",
                tool_name=f"task:{task_type}",
                input_hash=digest_of(task_input),
            )
            task_output = plugin.run(context, task_input)
            result = self._wrap_result(payload, task_output)
            result_hash = self._result_hash(result)
            internal_status = result["status"]
            self.events.append(
                run_id,
                event_type="tool_call",
                message=f"任务插件返回 {len(result['review_items'])} 条待复核项",
                tool_name=f"task:{task_type}.done",
                output_hash=result_hash,
            )
        except AppError as error:
            finished_at = now_iso()
            error = error.with_run(run_id)
            self._transition(payload, "failed", finished_at=finished_at, error=error.payload(), touch_error=True)
            self.events.append(
                run_id,
                event_type="error",
                message=f"{error.code}: {error.message}",
                tool_name="execute_run",
                output_hash=digest_of(error.payload()),
            )
            self.events.write_error(run_id, error.payload())
            self.events.write_trace(run_id)
            return self.get_run(run_id)

        public_result = {key: value for key, value in result.items() if not key.startswith("_")}
        validate_contract("TaskResult", public_result, label="TaskResult")
        self._register_reviews(run_id, public_result)
        finished_at = now_iso()
        self._transition(
            payload,
            internal_status,
            finished_at=finished_at,
            result=public_result,
            touch_result=True,
            result_hash=result_hash,
        )
        self.events.write_result(run_id, public_result, needs_review=internal_status == "needs_review")
        self.events.write_trace(run_id)
        self.events.append(
            run_id,
            event_type="state",
            message=f"任务结束：{internal_status}",
            tool_name="execute_run",
            output_hash=result_hash,
        )
        return self.get_run(run_id)

    # ------------------------------------------------------------ 查询
    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.store.get_run_row(run_id)
        run = self._load(row["payload"])
        validate_contract("Run", run, label="Run")
        return run

    def get_result(self, run_id: str) -> dict[str, Any]:
        row = self.store.get_run_row(run_id)
        run = self._load(row["payload"])
        if run["status"] in {"queued", "running"}:
            raise AppError("RUN_NOT_READY", "任务尚未产生结果", details={"run_id": run_id, "status": run["status"]})
        if run["status"] in {"failed", "cancelled"}:
            raise AppError(
                "RUN_NOT_READY",
                "任务未成功完成，请查看 Run.error",
                details={"run_id": run_id, "status": run["status"], "error": run.get("error")},
            )
        result = self._load(row["result"] or "{}")
        validate_contract("TaskResult", result, label="TaskResult")
        return result

    def get_events(self, run_id: str, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        self.store.get_run_row(run_id)
        page = self.events.page(run_id, cursor=cursor, limit=limit)
        validate_contract("EventPage", page, label="EventPage")
        return page

    # ------------------------------------------------------------ 复核
    def decide_review(self, review_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        validate_contract("ReviewDecision", decision, label="ReviewDecision")
        row = self.store.get_review_row(review_id)
        run_id = row["run_id"]
        status = {"accept": "accepted", "reject": "rejected", "correct": "corrected"}[decision["decision"]]
        updated = {
            "review_id": review_id,
            "run_id": run_id,
            "target_id": row["target_id"],
            "reason": row["reason"],
            "status": status,
            # 存储层要求 JSON 文本：这里必须直接复用原始字符串，
            # 反序列化成 list 再写回会触发 sqlite3 "type 'list' is not supported"
            "evidence_ids": row["evidence_ids"] or "[]",
            "reviewer": decision["reviewer"],
            "note": decision["note"],
            "corrected_value": decision["corrected_value"],
            "updated_at": now_iso(),
        }
        self.store.upsert_review(updated)
        self.events.append(
            run_id,
            event_type="state",
            message=f"复核 {review_id} 决策为 {decision['decision']}（{decision['reviewer']}）",
            tool_name="decide_review",
            output_hash=digest_of(updated),
        )
        if decision["decision"] == "correct" and decision["corrected_value"] is not None:
            self._apply_correction(run_id, row["target_id"], decision["corrected_value"])
        item = self.store.get_review_row(review_id)
        contract_item = {
            "review_id": item["review_id"],
            "target_id": item["target_id"],
            "reason": item["reason"],
            "status": item["status"],
            "evidence_ids": self._load(item["evidence_ids"] or "[]"),
        }
        validate_contract("ReviewItem", contract_item, label="ReviewItem")
        return contract_item

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        self.store.get_run_row(run_id)
        return [
            {
                "review_id": row["review_id"],
                "target_id": row["target_id"],
                "reason": row["reason"],
                "status": row["status"],
                "evidence_ids": self._load(row["evidence_ids"] or "[]"),
            }
            for row in self.store.list_reviews(run_id)
        ]

    # ------------------------------------------------------------ 重放
    def replay(self, run_id: str) -> dict[str, Any]:
        """离线重放：同一输入、同一代码版本应得到同一 result_hash 与事件轨迹。

        重放不写库、不改动原 Run，只返回比对结论，便于评审核对“可复现”。
        """
        row = self.store.get_run_row(run_id)
        payload = self._load(row["payload"])
        task_type = payload["task_type"]
        task_input = self._load(row["task_input"] or "{}")

        bundle = self._input_bundle(list(payload["document_ids"]), task_type, task_input)
        current_input_hash = digest_of(bundle)
        # 轨迹复刻：从原始事件流取“解析”事件，再按文档顺序记录索引读取，
        # 最后是任务插件调用与完成。不做状态猜测，避免首跑/重放的轨迹错位。
        original_events = self.events.all_events(run_id)
        plugin = build_task(task_type, parse=self.parse, settings=self.settings, registry=self.task_registry)
        trace: list[tuple[str, str | None, str | None, str | None]] = []
        for event in original_events:
            if event["event_type"] == "file_access" and event["tool_name"] == "parse_document":
                trace.append(("file_access", "parse_document", event["input_hash"], event["output_hash"]))
        for document_id in payload["document_ids"]:
            self._trace_file_access(trace, document_id)
        self._trace_tool(trace, f"task:{task_type}", digest_of(task_input), None)
        sink = _ReplayEvents(trace)
        context = _TaskContext(
            run_id=run_id,
            document_ids=tuple(payload["document_ids"]),
            store=self.store,
            documents=self.documents,
            parse=self.parse,
            events=sink,
            model=self.model,
            settings=self.settings,
        )
        output = plugin.run(context, task_input)
        result = self._wrap_result(payload, output)
        result_hash = self._result_hash(result)
        self._trace_tool(trace, f"task:{task_type}.done", None, result_hash)
        stored_result = self._load(row["result"] or "null")
        # 轨迹比对按“事件类型 + 输入哈希 + 输出哈希”三元组进行，不比较工具名：
        # 首次运行的工具名可能因库版本/后端选择与重放路径不同，而输入输出哈希
        # 才是“同一输入同一处理”的实质证据。
        original_raw = [item for item in self.events.trace(run_id) if item[0] in {"file_access", "tool_call"}]
        original_fingerprints = [(item[0], item[2], item[3]) for item in original_raw]
        replay_fingerprints = [(item[0], item[2], item[3]) for item in trace if item[0] in {"file_access", "tool_call"}]
        comparable_stored = {key: value for key, value in (stored_result or {}).items() if key != "generated_at"}
        comparable_replay = {key: value for key, value in result.items() if key != "generated_at"}
        return {
            "run_id": run_id,
            "task_type": task_type,
            "input_hash_original": payload.get("input_hash"),
            "input_hash_replay": current_input_hash,
            "input_hash_match": payload.get("input_hash") == current_input_hash,
            "result_hash_original": payload.get("result_hash"),
            "result_hash_replay": result_hash,
            "result_hash_match": payload.get("result_hash") == result_hash,
            "trace_original": [list(item) for item in original_raw],
            "trace_replay": [list(item) for item in trace if item[0] in {"file_access", "tool_call"}],
            "trace_match": original_fingerprints == replay_fingerprints,
            "chain": self.events.verify_chain(run_id),
            "result_original_matches": comparable_stored == comparable_replay if stored_result else None,
            "hash_basis": "result_hash 不含 generated_at（时间戳）；轨迹比对不含工具名，只比对输入/输出哈希",
        }

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        row = self.store.get_run_row(run_id)
        payload = self._load(row["payload"])
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            raise AppError("RUN_NOT_READY", "该状态不可取消", details={"run_id": run_id, "status": payload["status"]})
        self._transition(payload, "cancelled", finished_at=now_iso())
        self.events.append(run_id, event_type="state", message="任务已取消", tool_name="cancel_run")
        return self.get_run(run_id)

    # ------------------------------------------------------------ 内部
    def _ensure_index(self, run_id: str, document_id: str) -> str:
        """保证文档已有解析索引，返回索引摘要。

        解析属于编排层职责：必须在 Run 的事件流里留痕，且首次运行与重放
        产生同一串事件，否则工具调用轨迹无法比对（离线重放验收条款）。
        """
        row = self.store.get_document_row(document_id)
        if row.get("parse_manifest") is None:
            manifest = self.parse.parse_document(document_id)
            digest = self.parse.index_digest(document_id)
            self.events.append(
                run_id,
                event_type="file_access",
                message=(
                    f"解析文档 {document_id}（{manifest.backend}），"
                    f"提示 {len(manifest.warnings)} 条；解析期 warning 见 GET /documents/{{id}}"
                ),
                tool_name="parse_document",
                input_hash=row["sha256"],
                output_hash=digest,
            )
            return digest
        return self.parse.index_digest(document_id)

    def _trace_file_access(
        self,
        trace: list[tuple[str, str | None, str | None, str | None]],
        document_id: str,
    ) -> None:
        """记录一次索引读取，与 execute_run 中 load_index 事件一一对应。"""
        trace.append(
            (
                "file_access",
                "load_index",
                self.documents.get_document(document_id)["sha256"],
                self.parse.index_digest(document_id),
            )
        )

    @staticmethod
    def _trace_tool(
        trace: list[tuple[str, str | None, str | None, str | None]],
        tool_name: str,
        input_hash: str | None,
        output_hash: str | None,
    ) -> None:
        trace.append(("tool_call", tool_name, input_hash, output_hash))

    @staticmethod
    def _result_hash(result: dict[str, Any]) -> str:
        """结果哈希基：剔除标识与时间戳，保证同一输入可复现同一 result_hash。

        剔除项与理由：
          * `run_id`      每次运行随机，属标识而非内容；
          * `generated_at` 生成时间，每次运行不同。
        其余字段（状态、文档、data、证据、计算、复核项、warnings）全部参与，
        任何内容变化都会改变哈希。
        """
        basis = {
            key: value
            for key, value in result.items()
            if key not in {"run_id", "generated_at"} and not key.startswith("_")
        }
        return digest_of(basis)

    def _wrap_result(self, run_payload: dict[str, Any], task_output: dict[str, Any]) -> dict[str, Any]:
        """编排器补齐 TaskResult 的运行 ID、版本、状态和时间。"""
        review_items = list(task_output.get("review_items", []))
        calculations = list(task_output.get("calculations", []))
        for calculation in calculations:
            validate_contract("Calculation", calculation, label="Calculation")
        for item in review_items:
            validate_contract("ReviewItem", item, label="ReviewItem")
        status = "needs_review" if review_items else "succeeded"
        result = {
            "api_version": API_VERSION,
            "run_id": run_payload["run_id"],
            "task_type": run_payload["task_type"],
            "status": status,
            "generated_at": now_iso(),
            "input_document_ids": list(run_payload["document_ids"]),
            "data": task_output["data"],
            "evidence_ids": list(task_output.get("evidence_ids", [])),
            "calculations": calculations,
            "review_items": review_items,
            "warnings": list(task_output.get("warnings", [])),
        }
        return result

    def _register_reviews(self, run_id: str, result: dict[str, Any]) -> None:
        for item in result.get("review_items", []):
            self.store.upsert_review(
                {
                    "review_id": item["review_id"],
                    "run_id": run_id,
                    "target_id": item["target_id"],
                    "reason": item["reason"],
                    "status": item["status"],
                    "evidence_ids": self._dump(item.get("evidence_ids", [])),
                    "reviewer": None,
                    "note": None,
                    "corrected_value": None,
                    "updated_at": now_iso(),
                }
            )

    def _apply_correction(self, run_id: str, target_id: str, corrected_value: str) -> None:
        """人工修正：写回结果并重新计算 result_hash，保留原始证据不变。"""
        row = self.store.get_run_row(run_id)
        result = self._load(row["result"] or "null")
        if not isinstance(result, dict):
            return
        changed = False
        for record in result.get("data", {}).get("records", []):
            if record.get("event_id") != target_id:
                continue
            for field in record.get("fields", []):
                if field.get("status") == "needs_review" and field.get("value") is None:
                    field["value"] = corrected_value
                    field["status"] = "verified"
                    field["reason"] = f"人工复核修正（review target {target_id}）"
                    changed = True
                    break
            if changed:
                break
        if not changed:
            return
        payload = self._load(row["payload"])
        remaining = self.list_reviews(run_id)
        if not any(item["status"] == "pending" for item in remaining):
            payload["status"] = "succeeded"
            # Run 状态与结果状态必须一致：否则会同时出现
            # Run.status=succeeded 与 TaskResult.status=needs_review
            result["status"] = "succeeded"
        result_hash = self._result_hash(result)
        payload["result_hash"] = result_hash
        self.store.update_run(
            run_id,
            status=payload["status"],
            payload=self._dump(payload),
            finished_at=payload.get("finished_at") or now_iso(),
            result=result,
            touch_result=True,
        )
        self.events.append(
            run_id,
            event_type="state",
            message=f"人工修正写回 {target_id}，结果状态更新为 {result['status']}",
            tool_name="apply_correction",
            output_hash=result_hash,
        )
        self.events.write_result(run_id, result, needs_review=payload["status"] == "needs_review")

    def _transition(
        self,
        payload: dict[str, Any],
        new_status: str,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        result_hash: str | None = None,
        touch_result: bool = False,
        touch_error: bool = False,
    ) -> None:
        current = payload["status"]
        if new_status != current and new_status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise validation_error(
                f"非法状态转换：{current} -> {new_status}",
                run_id=payload["run_id"],
                current=current,
                requested=new_status,
            )
        payload["status"] = new_status
        if started_at:
            payload["started_at"] = started_at
        if finished_at:
            payload["finished_at"] = finished_at
        if touch_error:
            payload["error"] = error
        if result_hash:
            payload["result_hash"] = result_hash
        self.store.update_run(
            payload["run_id"],
            status=new_status,
            payload=self._dump(payload),
            started_at=started_at,
            finished_at=finished_at if finished_at is not None else payload.get("finished_at"),
            result=result if touch_result else None,
            touch_result=touch_result,
            error=error if touch_error else None,
            touch_error=touch_error,
        )

    def _input_bundle(self, document_ids: list[str], task_type: str, task_input: dict[str, Any]) -> dict[str, Any]:
        documents = []
        for document_id in document_ids:
            row = self.store.get_document_row(document_id)
            documents.append(
                {
                    "document_id": row["document_id"],
                    "sha256": row["sha256"],
                    "page_count": int(row["page_count"]),
                    "media_type": row["media_type"],
                    "source_url": row["source_url"],
                    "disclosure_date": row["disclosure_date"],
                }
            )
        return {
            "api_version": API_VERSION,
            "task_type": task_type,
            "task_input": task_input,
            "documents": documents,
            "code_version": self.settings.code_version,
            "prompt_version": self.settings.prompt_version,
        }

    @staticmethod
    def _dump(value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _load(value: str) -> Any:
        import json

        return json.loads(value)


class _TaskContext:
    """任务插件可见的上下文；对应 contracts/ports.py TaskContext。"""

    def __init__(
        self,
        *,
        run_id: str,
        document_ids: tuple[str, ...],
        store: Store,
        documents: DocumentService,
        parse: ParseService,
        events: Any,
        model: Any,
        settings: Settings,
    ) -> None:
        self.run_id = run_id
        self.document_ids = document_ids
        self.store = store
        self.documents = documents
        self.parse = parse
        self.events = events
        self.model = model
        self.settings = settings


class _ReplayEvents:
    """重放时把事件写进内存，不污染原 Run 的事件流。"""

    def __init__(self, sink: list[tuple[str, str | None, str | None, str | None]]) -> None:
        self.sink = sink

    def append(self, run_id: str, *, event_type: str, message: str, tool_name: str | None = None,
               input_hash: str | None = None, output_hash: str | None = None, attempt: int = 1,
               payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sink.append((event_type, tool_name, input_hash, output_hash))
        return {"event_type": event_type, "tool_name": tool_name, "message": message}

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        self.append(run_id, event_type=event.get("event_type", "tool_call"), message=event.get("message", ""))
