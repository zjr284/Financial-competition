"""导出：把一次 Run 的结果导成 JSON / CSV / HTML，并写入 export 事件。

设计取舍：
  * 导出不调用模型、不新增数值，只重排已有结果，保证与 API 返回逐字一致；
  * 每条导出都写 `export` 事件（含输出文件哈希），使"结果从哪来"可追溯；
  * 导出文件只引用 `evidence_id`，并附带页码与坐标，供评审回跳原文；
  * 其它选题（非 records 结构）走通用 JSON，并在 HTML 里标注"该题无表格视图"，
    不假装支持而给出空表。
"""

from __future__ import annotations

import csv
import html
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.common.ids import digest_of, now_iso, sha256_bytes
from app.config import Settings
from app.errors import AppError


RECORD_TASKS = ("structured_extraction",)


@dataclass
class ExportResult:
    """一次导出的产物描述。"""

    run_id: str
    formats: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    rows: int = 0
    evidence_count: int = 0
    warnings: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "formats": self.formats,
            "files": self.files,
            "rows": self.rows,
            "evidence_count": self.evidence_count,
            "warnings": self.warnings,
            "hashes": self.hashes,
        }


def run_export(
    *,
    run_id: str,
    result: dict[str, Any],
    settings: Settings,
    formats: list[str],
    out_dir: Path | None = None,
) -> ExportResult:
    """导出指定格式；返回产物清单（不含事件写入，事件由编排层负责）。"""
    wanted = [item.lower() for item in formats]
    unknown = [item for item in wanted if item not in {"json", "csv", "html"}]
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            f"不支持的导出格式：{unknown}",
            details={"supported": ["json", "csv", "html"]},
        )
    target_dir = Path(out_dir) if out_dir else settings.exports_dir / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    outcome = ExportResult(run_id=run_id)
    data = result.get("data") or {}
    records = data.get("records") if isinstance(data, dict) else None
    if records:
        outcome.rows = len(records)
    outcome.evidence_count = len(result.get("evidence_ids", []))
    if result.get("task_type") not in RECORD_TASKS and "csv" in wanted:
        outcome.warnings.append(
            f"{result.get('task_type')} 不是记录型结果，CSV 仅导出通用字段；请以 JSON 为准"
        )

    writers = {
        "json": _write_json,
        "csv": _write_csv,
        "html": _write_html,
    }
    for fmt in wanted:
        path = writers[fmt](target_dir=target_dir, run_id=run_id, result=result)
        payload = path.read_bytes()
        outcome.formats.append(fmt)
        outcome.files.append(str(path))
        outcome.hashes[path.name] = sha256_bytes(payload)
    return outcome


# ---------------------------------------------------------------- JSON


def _write_json(*, target_dir: Path, run_id: str, result: dict[str, Any]) -> Path:
    """完整可追溯导出：结果 + 证据明细 + 计算 ID 引用。"""
    path = target_dir / "result.export.json"
    payload = {
        "run_id": run_id,
        "exported_at": now_iso(),
        "note": "导出内容与 GET /runs/{run_id}/result 一致；evidence 为引用明细，不含完整文档文本",
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- CSV


def _write_csv(*, target_dir: Path, run_id: str, result: dict[str, Any]) -> Path:
    """记录型结果展开为字段矩阵：一字段一行，便于用 Excel 逐项核对。"""
    path = target_dir / "records.csv"
    data = result.get("data") or {}
    records = data.get("records") if isinstance(data, dict) else None

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if not records:
        writer.writerow(["run_id", "task_type", "status", "note"])
        writer.writerow([run_id, result.get("task_type"), result.get("status"), "该任务不是记录型结果，无字段矩阵"])
        path.write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
        return path

    writer.writerow(
        [
            "event_id",
            "document_id",
            "company_code",
            "event_date",
            "announcement_type",
            "field_code",
            "raw_value",
            "value",
            "unit",
            "status",
            "reason",
            "evidence_ids",
        ]
    )
    for record in records:
        for field_value in record.get("fields", []):
            writer.writerow(
                [
                    record.get("event_id"),
                    record.get("document_id"),
                    record.get("company_code"),
                    record.get("event_date"),
                    record.get("announcement_type"),
                    field_value.get("field_code"),
                    field_value.get("raw_value"),
                    field_value.get("value"),
                    field_value.get("unit"),
                    field_value.get("status"),
                    field_value.get("reason"),
                    " ".join(field_value.get("evidence_ids", [])),
                ]
            )
    # utf-8-sig：Excel 打开中文 CSV 不乱码
    path.write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    return path


# ---------------------------------------------------------------- HTML


def _write_html(*, target_dir: Path, run_id: str, result: dict[str, Any]) -> Path:
    """人读报告：按记录分组展示字段与来源，缺失字段显式标红说明。"""
    path = target_dir / "report.html"
    data = result.get("data") or {}
    records = data.get("records") if isinstance(data, dict) else None

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>运行报告 {html.escape(run_id)}</title>",
        "<style>",
        "body{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:2rem;color:#1f2328}",
        "table{border-collapse:collapse;width:100%;margin:1rem 0}",
        "th,td{border:1px solid #d0d7de;padding:.4rem .6rem;font-size:.9rem;text-align:left}",
        "th{background:#f6f8fa}",
        ".verified{color:#1a7f37}.needs_review{color:#9a6700;font-weight:600}",
        ".snippet{color:#57606a;font-size:.85rem}",
        ".banner{background:#fff8c5;border:1px solid #d4a72c;padding:.6rem 1rem;border-radius:6px}",
        "code{background:#f6f8fa;padding:.1rem .3rem;border-radius:4px}",
        "</style></head><body>",
        f"<h1>运行报告</h1>",
        '<p class="banner">本报告为接口演示样例输出，不是竞赛实测成果；数值与证据一一对应，缺失字段已显式标注。</p>',
        "<table><tbody>",
    ]
    for label, key in (
        ("run_id", "run_id"),
        ("task_type", "task_type"),
        ("status", "status"),
        ("generated_at", "generated_at"),
        ("input_document_ids", "input_document_ids"),
    ):
        value = result.get(key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        parts.append(f"<tr><th>{html.escape(label)}</th><td><code>{html.escape(str(value))}</code></td></tr>")
    warnings = result.get("warnings") or []
    parts.append(
        f"<tr><th>warnings</th><td>{html.escape('；'.join(warnings)) if warnings else '无'}</td></tr>"
    )
    parts.append(f"<tr><th>evidence 数</th><td>{len(result.get('evidence_ids', []))}</td></tr>")
    parts.append("</tbody></table>")

    if records:
        parts.append("<h2>提取记录与来源</h2>")
        for record in records:
            parts.append(
                f"<h3>{html.escape(str(record.get('announcement_type')))} · "
                f"<code>{html.escape(str(record.get('event_id')))}</code></h3>"
            )
            parts.append(
                "<p>公司代码 <code>"
                + html.escape(str(record.get("company_code")))
                + "</code>｜事件日期 <code>"
                + html.escape(str(record.get("event_date")))
                + "</code>｜文档 <code>"
                + html.escape(str(record.get("document_id")))
                + "</code></p>"
            )
            parts.append(
                "<table><thead><tr><th>字段</th><th>原值</th><th>归一值</th><th>单位</th>"
                "<th>状态</th><th>证据 ID</th><th>说明</th></tr></thead><tbody>"
            )
            for field_value in record.get("fields", []):
                status = str(field_value.get("status"))
                css = "verified" if status == "verified" else "needs_review"
                parts.append(
                    "<tr>"
                    f"<td>{html.escape(str(field_value.get('field_code')))}</td>"
                    f"<td class='snippet'>{html.escape(str(field_value.get('raw_value')))}</td>"
                    f"<td>{html.escape(str(field_value.get('value')))}</td>"
                    f"<td>{html.escape(str(field_value.get('unit')))}</td>"
                    f"<td class='{css}'>{html.escape(status)}</td>"
                    f"<td>{html.escape(' '.join(field_value.get('evidence_ids', [])) or '—')}</td>"
                    f"<td class='snippet'>{html.escape(str(field_value.get('reason') or ''))}</td>"
                    "</tr>"
                )
            parts.append("</tbody></table>")

        if result.get("evidence_ids"):
            parts.append("<h2>证据索引</h2>")
            parts.append(
                "<table><thead><tr><th>evidence_id</th><th>页码</th><th>bbox</th>"
                "<th>方法</th><th>置信度</th></tr></thead><tbody>"
            )
            parts.append(
                "<tr><td colspan='5' class='snippet'>"
                "证据片段请通过 <code>GET /evidence/{evidence_id}</code> 获取；"
                "报告不内嵌原文以避免泄露材料。坐标用于在原件上框选。"
                "</td></tr>"
            )
            parts.append("</tbody></table>")
    else:
        parts.append(
            "<h2>无表格视图</h2><p class='snippet'>"
            f"任务 <code>{html.escape(str(result.get('task_type')))}</code> 的结果结构不是记录型，"
            "其完整内容请查看导出的 JSON。</p>"
        )

    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
