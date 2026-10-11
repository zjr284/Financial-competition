"""structured_extraction 任务插件（成员 A 主责，成员 C 标注）。

输入（契约 ExtractionInput）：announcement_types 数组。
输出（契约 ExtractionData）：records，每条含 event_id, document_id, company_code,
event_date, announcement_type, fields。
数值一律来自确定性归一化，并逐个字段挂 evidence_ids；缺失字段保留并标 needs_review。

event_id 采用契约 $defs.Id 的 `event_` 家族（稳定内容派生）。
原因：`TaskResult.review_items[].target_id` 必须满足 `$defs.Id` 正则，而结构化提取的
复核对象就是记录本身，因此把事件 ID 放进 `event_` 家族可以让“记录 ↔ 复核项”直接引用，
不必发明新前缀。该约定已记入 docs/接口问题记录.md，供 B/C 统一。
"""

from __future__ import annotations

from typing import Any

from app.common.contracts import validate_contract
from app.common.ids import digest_of, make_id
from app.errors import validation_error
from app.parse.service import ParseService
from app.tasks.structured_extraction.dictionary import ANNOUNCEMENT_TYPES, TYPE_LABELS
from app.tasks.structured_extraction.extract import ExtractionEngine


class StructuredExtractionTask:
    task_type = "structured_extraction"

    def __init__(self, parse: ParseService, settings: Any) -> None:
        self.parse = parse
        self.settings = settings

    # ------------------------------------------------------------ 入口
    def run(self, context: Any, task_input: dict[str, Any]) -> dict[str, Any]:
        validate_contract("ExtractionInput", task_input, label="structured_extraction.task_input")
        announcement_types = list(task_input["announcement_types"])
        document_ids: list[str] = list(context.document_ids)

        engine = ExtractionEngine()
        warnings: list[str] = []
        records: list[dict[str, Any]] = []
        evidence_map: dict[str, dict[str, Any]] = {}

        for document_id in document_ids:
            row = self.parse.store.get_document_row(document_id)
            index = self.parse.load_index(row)
            if index is None:
                # 正常情况下编排层已解析完毕；此处兜底不写事件、不写 warnings，
                # 以保持结果内容与运行次数无关（离线重放要求 result_hash 稳定）。
                self.parse.parse_document(document_id)
                row = self.parse.store.get_document_row(document_id)
                index = self.parse.load_index(row)
            if index is None:
                warnings.append(f"文档 {document_id} 无法建立索引，跳过")
                continue

            # 纯表格文档走表头-数据行路径
            table_result = None
            if all(line.kind == "table" for page in index.pages for line in page.lines) and any(
                page.lines for page in index.pages
            ):
                table_result = engine.extract_table_document(document_id, index, announcement_types)
            result = table_result or engine.extract(document_id, index, announcement_types)
            records.extend(result["records"])
            warnings.extend(result["warnings"])
            for evidence in result["evidence"]:
                validate_contract("Evidence", evidence, label="Evidence")
                evidence_map.setdefault(evidence["evidence_id"], evidence)

        self.parse.store.insert_evidence(list(evidence_map.values()))

        review_items: list[dict[str, Any]] = []
        for record in records:
            pending = [field for field in record["fields"] if field["status"] == "needs_review"]
            if not pending:
                continue
            codes = ", ".join(field["field_code"] for field in pending)
            review_items.append(
                {
                    "review_id": make_id("review", record["event_id"], codes, length=20),
                    "target_id": record["event_id"],
                    "reason": f"{TYPE_LABELS.get(record['announcement_type'], record['announcement_type'])}记录存在待复核字段：{codes}",
                    "status": "pending",
                    "evidence_ids": [
                        evidence_id
                        for field in pending
                        for evidence_id in field["evidence_ids"]
                    ][:5],
                }
            )

        data = {"records": records}
        if not records:
            warnings.append("未识别到任何目标类型公告记录，请确认资料与 announcement_types 是否匹配")
            review_items.append(
                {
                    "review_id": make_id("review", "no_record", sorted(announcement_types), length=20),
                    "target_id": document_ids[0] if document_ids else "run",
                    "reason": "结构化提取未产出任何记录，需要人工确认资料范围与公告类型",
                    "status": "pending",
                    "evidence_ids": [],
                }
            )

        payload = {
            "data": data,
            "evidence_ids": sorted(evidence_map.keys()),
            "calculations": [],
            "review_items": review_items,
            "warnings": list(dict.fromkeys(warnings)),
        }
        validate_contract("ExtractionData", data, label="ExtractionData")
        return payload

    # ------------------------------------------------------------ 辅助
    @staticmethod
    def supported_types() -> tuple[str, ...]:
        return ANNOUNCEMENT_TYPES

    @staticmethod
    def input_digest(task_input: dict[str, Any]) -> str:
        return digest_of(task_input)


def build(parse: ParseService, settings: Any) -> StructuredExtractionTask:
    return StructuredExtractionTask(parse, settings)
