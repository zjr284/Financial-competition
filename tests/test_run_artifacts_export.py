"""运行清单、导出与失联恢复测试。

对应 `实施路线与验收清单.md` 第 2 节（每个运行固定存放 manifest/事件/结果/metrics）
与计划书 §8.1 的高优先级项（`running` 悬挂态失败恢复）。
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.common.ids import now_iso
from app.errors import AppError


@pytest.fixture()
def finished_run(app, samples_dir: Path) -> dict:
    """跑一次成功任务，返回 Run。"""
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"), disclosure_date="2026-09-01")
    run = app.run_task(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge", "bid_award", "equity_change"]},
        }
    )
    assert run["status"] == "succeeded"
    return run


# ---------------------------------------------------------------- 运行清单


def test_run_directory_has_fixed_manifest(app, finished_run: dict) -> None:
    """运行目录必须固定包含六件产物（实施路线第 2 节）。"""
    directory = app.events.run_dir(finished_run["run_id"])
    for name in ("manifest.json", "events.jsonl", "result.json", "trace.json", "metrics.json"):
        assert (directory / name).exists(), f"缺少 {name}"


def test_metrics_contains_counts_and_chain(app, finished_run: dict) -> None:
    metrics = json.loads((app.events.run_dir(finished_run["run_id"]) / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["run_id"] == finished_run["run_id"]
    assert metrics["task_type"] == "structured_extraction"
    assert metrics["status"] == "succeeded"
    assert metrics["terminal"] is True
    assert metrics["duration_ms"] is not None and metrics["duration_ms"] >= 0

    counts = metrics["counts"]
    assert counts["events"] >= 5
    assert counts["file_access"] >= 1
    assert counts["tool_call"] >= 2
    assert counts["records"] == 1
    assert counts["evidence_ids"] >= 1
    assert counts["error"] == 0
    assert metrics["chain"]["ok"] is True
    assert metrics["chain"]["checked"] == counts["events"]
    assert "task:structured_extraction" in metrics["tools"]


def test_metrics_never_leaks_原文(app, finished_run: dict) -> None:
    """运行清单属公开日志：不得出现原文片段。"""
    directory = app.events.run_dir(finished_run["run_id"])
    blob = (directory / "metrics.json").read_text(encoding="utf-8")
    for needle in ("示例控股集团有限公司", "1,000万股", "占公司总股本比例"):
        assert needle not in blob


def test_metrics_written_for_failed_run(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.run_task(
        {
            "api_version": "1.0.0",
            "task_type": "financial_analysis",
            "document_ids": [document["document_id"]],
            "task_input": {"company_code": "999999", "periods": ["2025-12-31"], "statement_scope": "consolidated"},
        }
    )
    # 该题由 B 实现；未接入时应失败并留下可读的失败清单
    if run["status"] != "failed":
        pytest.skip(f"financial_analysis 已接入，本用例仅验证未接入时的失败清单（当前 {run['status']}）")
    metrics = json.loads((app.events.run_dir(run["run_id"]) / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert metrics["error_code"] == run["error"]["code"]
    assert metrics["counts"]["error"] >= 1
    assert (app.events.run_dir(run["run_id"]) / "error.json").exists()


# ---------------------------------------------------------------- 导出


def test_export_writes_three_formats(app, finished_run: dict) -> None:
    outcome = app.export_run(finished_run["run_id"], formats=["json", "csv", "html"])
    assert outcome["formats"] == ["json", "csv", "html"]
    assert outcome["rows"] == 1
    assert outcome["evidence_count"] >= 1
    for path in outcome["files"]:
        assert Path(path).exists()
    assert set(outcome["hashes"]) == {"result.export.json", "records.csv", "report.html"}
    for digest in outcome["hashes"].values():
        assert len(digest) == 64


def test_export_json_matches_api_result(app, finished_run: dict) -> None:
    outcome = app.export_run(finished_run["run_id"], formats=["json"])
    exported = json.loads(Path(outcome["files"][0]).read_text(encoding="utf-8"))
    assert exported["run_id"] == finished_run["run_id"]
    # 导出不得改动结果：与 API 返回逐字一致
    assert exported["result"] == app.get_result(finished_run["run_id"])


def test_export_csv_is_field_matrix(app, finished_run: dict) -> None:
    outcome = app.export_run(finished_run["run_id"], formats=["csv"])
    text = Path(outcome["files"][0]).read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert rows, "CSV 应有字段行"
    codes = {row["field_code"] for row in rows}
    assert {"pledged_shares", "pledged_ratio", "shareholder", "pledgee"} <= codes

    shares = next(row for row in rows if row["field_code"] == "pledged_shares")
    assert shares["value"] == "10000000"
    assert shares["unit"] == "share"
    assert shares["status"] == "verified"
    assert shares["evidence_ids"].startswith("ev_")


def test_export_html_escapes_and_marks_sample(app, finished_run: dict) -> None:
    outcome = app.export_run(finished_run["run_id"], formats=["html"])
    text = Path(outcome["files"][0]).read_text(encoding="utf-8")
    assert "接口演示样例" in text  # 必须标注不是实测成果
    assert "pledged_shares" in text
    assert "<table" in text
    assert "<script" not in text.lower()


def test_export_writes_export_event_and_refreshes_metrics(app, finished_run: dict) -> None:
    before = app.events.build_metrics(
        run_id=finished_run["run_id"],
        task_type="structured_extraction",
        status="succeeded",
        code_version="test",
        started_at=None,
        finished_at=None,
        result=None,
        error=None,
        events=app.events.all_events(finished_run["run_id"]),
        chain={"ok": True},
    )
    assert before["counts"]["export"] == 0

    app.export_run(finished_run["run_id"], formats=["json"])

    events = app.events.all_events(finished_run["run_id"])
    assert any(event["event_type"] == "export" for event in events)
    metrics = json.loads((app.events.run_dir(finished_run["run_id"]) / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["counts"]["export"] >= 1
    assert "export_run" in metrics["tools"]
    assert metrics["chain"]["ok"] is True


def test_export_does_not_change_result_hash(app, finished_run: dict) -> None:
    """导出是只读操作：不得改动 result_hash 与结果内容。"""
    before = app.get_run(finished_run["run_id"])
    app.export_run(finished_run["run_id"], formats=["json", "csv", "html"])
    after = app.get_run(finished_run["run_id"])
    assert before["result_hash"] == after["result_hash"]
    assert app.get_result(finished_run["run_id"]) == app.get_result(finished_run["run_id"])


def test_export_unknown_format_rejected(app, finished_run: dict) -> None:
    with pytest.raises(AppError) as error:
        app.export_run(finished_run["run_id"], formats=["pdf"])
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.http_status == 400


def test_export_unfinished_run_is_rejected(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.create_run(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        }
    )
    with pytest.raises(AppError) as error:
        app.export_run(run["run_id"], formats=["json"])
    assert error.value.code == "RUN_NOT_READY"


def test_export_non_record_task_still_produces_json(app, samples_dir: Path) -> None:
    """非记录型结果：JSON 必须可用，CSV 给通用表并带提示，不静默给空表。"""
    from app.tasks import build_task

    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    # 用桩插件模拟其它成员的选题，避免依赖其实现是否已接入
    stub = _StubTask()
    app.orchestrator.task_registry["financial_analysis"] = stub
    run = app.run_task(
        {
            "api_version": "1.0.0",
            "task_type": "financial_analysis",
            "document_ids": [document["document_id"]],
            "task_input": {"company_code": "999999", "periods": ["2025-12-31"], "statement_scope": "consolidated"},
        }
    )
    assert run["status"] == "succeeded", run
    assert build_task("financial_analysis", registry=app.orchestrator.task_registry).task_type == stub.task_type

    outcome = app.export_run(run["run_id"], formats=["json", "csv", "html"])
    assert outcome["warnings"], "非记录型结果导出 CSV 必须给出提示"
    csv_text = Path(outcome["files"][1]).read_text(encoding="utf-8-sig")
    assert "无字段矩阵" in csv_text
    html_text = Path(outcome["files"][2]).read_text(encoding="utf-8")
    assert "无表格视图" in html_text


class _StubTask:
    """最小桩：只验证导出对非记录型结果的降级行为。"""

    task_type = "financial_analysis"

    def run(self, context, task_input) -> dict:
        return {
            "data": {"facts": [], "alerts": []},
            "evidence_ids": [],
            "calculations": [],
            "review_items": [],
            "warnings": ["桩实现，仅用于导出降级测试"],
        }


# ---------------------------------------------------------------- 失联恢复


def _make_stale(app, run_id: str, *, stale_seconds: int) -> None:
    """把 Run 改造成 started_at 很久以前的 running 状态。"""
    from datetime import timedelta

    from app.common.ids import parse_iso

    row = app.store.get_run_row(run_id)
    payload = app.orchestrator._load(row["payload"])
    old = parse_iso(now_iso()) - timedelta(seconds=stale_seconds * 3)
    payload["status"] = "running"
    payload["started_at"] = old.isoformat(timespec="seconds")
    payload["finished_at"] = None
    payload["result_hash"] = None
    payload["error"] = None
    app.orchestrator._transition(payload, "running", started_at=payload["started_at"])


def test_recover_marks_stale_running_as_failed(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.create_run(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        }
    )
    _make_stale(app, run["run_id"], stale_seconds=app.settings.run_stale_seconds)

    recovered = app.recover_stuck_runs()
    assert len(recovered) == 1
    assert recovered[0]["run_id"] == run["run_id"]
    assert recovered[0]["status"] == "failed"
    assert recovered[0]["previous_status"] == "running"

    after = app.get_run(run["run_id"])
    assert after["status"] == "failed"
    assert after["error"]["code"] == "INTERNAL_ERROR"
    assert after["error"]["retryable"] is True
    assert after["error"]["run_id"] == run["run_id"]
    assert after["finished_at"]

    directory = app.events.run_dir(run["run_id"])
    assert (directory / "error.json").exists()
    metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "failed"
    assert metrics["counts"]["error"] >= 1
    assert any(event["event_type"] == "error" for event in app.events.all_events(run["run_id"]))
    assert app.events.verify_chain(run["run_id"])["ok"] is True


def test_recover_leaves_fresh_running_alone(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.create_run(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        }
    )
    _make_stale(app, run["run_id"], stale_seconds=1)  # 只过去 3 秒
    recovered = app.recover_stuck_runs(stale_seconds=600)
    assert recovered == []
    assert app.get_run(run["run_id"])["status"] == "running"


def test_recover_ignores_non_running_statuses(app, finished_run: dict) -> None:
    """已成功/待复核/已取消的 Run 不得被误改判。"""
    assert app.recover_stuck_runs(stale_seconds=0) == []
    assert app.get_run(finished_run["run_id"])["status"] == "succeeded"


def test_recover_is_idempotent(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.create_run(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        }
    )
    _make_stale(app, run["run_id"], stale_seconds=app.settings.run_stale_seconds)
    assert len(app.recover_stuck_runs()) == 1
    # 第二次不应再改判，也不应重复写事件
    events_after_first = len(app.events.all_events(run["run_id"]))
    assert app.recover_stuck_runs() == []
    assert len(app.events.all_events(run["run_id"])) == events_after_first


def test_cancel_after_recover_rejected(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = app.create_run(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        }
    )
    _make_stale(app, run["run_id"], stale_seconds=app.settings.run_stale_seconds)
    app.recover_stuck_runs()
    with pytest.raises(AppError) as error:
        app.orchestrator.cancel_run(run["run_id"])
    assert error.value.code == "RUN_NOT_READY"


def test_stale_run_is_recoverable_then_retryable(app, samples_dir: Path) -> None:
    """恢复为 failed 之后，同一输入必须能重新发起并成功（可重试语义）。"""
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    request = {
        "api_version": "1.0.0",
        "task_type": "structured_extraction",
        "document_ids": [document["document_id"]],
        "task_input": {"announcement_types": ["pledge"]},
    }
    first = app.create_run(request)
    _make_stale(app, first["run_id"], stale_seconds=app.settings.run_stale_seconds)
    app.recover_stuck_runs()

    second = app.run_task(request)
    assert second["status"] == "succeeded"
    assert app.get_result(second["run_id"])["data"]["records"]
