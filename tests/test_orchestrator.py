"""编排器测试：状态机、TaskResult 组装、事件哈希链、离线重放、人工复核回写。

验收条款（`docs/给成员和AI助手的任务说明.md` 第 2 节）：
离线重放可得到同一输入哈希与工具调用轨迹；运行状态转换只允许契约规定的路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.common.contracts import validate_contract
from app.errors import AppError
from app.orchestrator.service import ALLOWED_TRANSITIONS


def _request(document_ids: list[str], task_type: str = "structured_extraction", **task_input) -> dict:
    return {
        "api_version": "1.0.0",
        "task_type": task_type,
        "document_ids": document_ids,
        "task_input": task_input or {"announcement_types": ["pledge", "bid_award", "equity_change"]},
    }


@pytest.fixture()
def pledge_document(app, samples_dir: Path) -> str:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"), disclosure_date="2026-09-01")
    return document["document_id"]


# ---------------------------------------------------------------- Run 生命周期


def test_create_run_returns_contract_run(app, pledge_document: str) -> None:
    run = app.create_run(_request([pledge_document]))
    validate_contract("Run", run, label="Run")
    assert run["status"] == "queued"
    assert run["api_version"] == "1.0.0"
    assert run["started_at"] is None and run["finished_at"] is None
    assert run["result_hash"] is None and run["error"] is None
    assert run["input_hash"] and len(run["input_hash"]) == 64
    # 运行清单落盘
    manifest = app.events.run_dir(run["run_id"]) / "manifest.json"
    assert manifest.exists()


def test_full_lifecycle_sets_timestamps_and_result_hash(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    assert run["status"] == "succeeded"
    assert run["started_at"] and run["finished_at"]
    assert run["result_hash"] and len(run["result_hash"]) == 64
    assert run["error"] is None

    result = app.get_result(run["run_id"])
    validate_contract("TaskResult", result, label="TaskResult")
    assert result["run_id"] == run["run_id"]
    assert result["status"] == "succeeded"
    assert result["input_document_ids"] == [pledge_document]
    # 结果文件落盘，命名区分是否待复核
    assert (app.events.run_dir(run["run_id"]) / "result.json").exists()


def test_result_before_finish_raises_run_not_ready(app, pledge_document: str) -> None:
    run = app.create_run(_request([pledge_document]))
    with pytest.raises(AppError) as error:
        app.get_result(run["run_id"])
    assert error.value.code == "RUN_NOT_READY"
    assert error.value.http_status == 409


def test_cancel_from_queued(app, pledge_document: str) -> None:
    run = app.create_run(_request([pledge_document]))
    cancelled = app.orchestrator.cancel_run(run["run_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"]
    with pytest.raises(AppError) as error:
        app.get_result(run["run_id"])
    assert error.value.code == "RUN_NOT_READY"


def test_illegal_transition_is_rejected(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    payload = app.orchestrator._load(app.store.get_run_row(run["run_id"])["payload"])
    with pytest.raises(AppError) as error:
        app.orchestrator._transition(payload, "running")
    assert error.value.code == "VALIDATION_ERROR"


def test_transition_whitelist_matches_contract() -> None:
    # 契约：queued → running → succeeded/needs_review/failed；needs_review 可回 running；
    # 未完成状态可 cancelled；终态不可再转换。
    assert ALLOWED_TRANSITIONS["queued"] >= {"running", "cancelled", "failed"}
    assert ALLOWED_TRANSITIONS["running"] >= {"succeeded", "needs_review", "failed", "cancelled"}
    assert ALLOWED_TRANSITIONS["needs_review"] >= {"running", "succeeded", "failed", "cancelled"}
    for terminal in ("succeeded", "failed", "cancelled"):
        assert ALLOWED_TRANSITIONS[terminal] == set()


def test_review_status_propagates_to_run(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = app.run_task(_request([document["document_id"]], announcement_types=["pledge"]))
    assert run["status"] == "needs_review"
    result = app.get_result(run["run_id"])
    assert result["status"] == "needs_review"
    assert (app.events.run_dir(run["run_id"]) / "result.needs_review.json").exists()


def test_unknown_task_type_is_rejected(app, pledge_document: str) -> None:
    with pytest.raises(AppError) as error:
        app.create_run(
            {"api_version": "1.0.0", "task_type": "not_a_task", "document_ids": [pledge_document], "task_input": {}}
        )
    assert error.value.code == "VALIDATION_ERROR"


def test_missing_document_is_rejected(app) -> None:
    with pytest.raises(AppError) as error:
        app.create_run(_request(["doc_does_not_exist"]))
    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_unimplemented_task_fails_with_owner_hint(app, pledge_document: str) -> None:
    """其他成员的选题未接入时，必须失败并注明主责，不得返回编造结果。"""
    run = app.run_task(_request([pledge_document], "financial_analysis", company_code="999999", periods=["2025-12-31"], statement_scope="consolidated"))
    assert run["status"] == "failed"
    validate_contract("Run", run, label="Run")
    assert run["error"]["code"] == "MISSING_INPUT"
    assert run["error"]["run_id"] == run["run_id"]
    assert "B" in run["error"]["message"]
    assert (app.events.run_dir(run["run_id"]) / "error.json").exists()


def test_invalid_task_input_is_rejected_at_creation(app, pledge_document: str) -> None:
    """无效的 task_input 属调用方输入问题：按契约返回 400，而不是变成一次失败的 Run。"""
    with pytest.raises(AppError) as error:
        app.run_task(_request([pledge_document], announcement_types=["not_a_real_type"]))
    assert error.value.code == "VALIDATION_ERROR"
    assert error.value.http_status == 400


# ---------------------------------------------------------------- 事件日志


def test_events_cover_required_types(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    page = app.get_events(run["run_id"])
    validate_contract("EventPage", page, label="EventPage")
    kinds = {event["event_type"] for event in page["events"]}
    assert {"state", "file_access", "tool_call"} <= kinds
    for event in page["events"]:
        validate_contract("Event", event, label="Event")
        assert event["run_id"] == run["run_id"]
        assert event["message"]
        # 日志不写完整文档文本：单条摘要必须是短句
        assert len(event["message"]) <= 241


def test_event_chain_detects_tampering(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    assert app.events.verify_chain(run["run_id"])["ok"] is True

    with app.store.write() as conn:
        conn.execute(
            "UPDATE events SET message = ? WHERE run_id = ? AND seq = (SELECT MIN(seq) FROM events WHERE run_id = ?)",
            ("被改写的消息", run["run_id"], run["run_id"]),
        )
    broken = app.events.verify_chain(run["run_id"])
    assert broken["ok"] is False
    assert broken["broken_at"] is not None


def test_event_pagination(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    first = app.get_events(run["run_id"], limit=2)
    assert len(first["events"]) == 2
    assert first["next_cursor"] is not None
    second = app.get_events(run["run_id"], cursor=first["next_cursor"], limit=2)
    assert second["events"]
    assert {event["event_id"] for event in first["events"]} & {event["event_id"] for event in second["events"]} == set()


def test_events_jsonl_and_trace_files(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    directory = app.events.run_dir(run["run_id"])
    for name in ("manifest.json", "events.jsonl", "result.json", "trace.json"):
        assert (directory / name).exists(), name


# ---------------------------------------------------------------- 离线重放


def test_replay_is_deterministic(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    report = app.orchestrator.replay(run["run_id"])
    assert report["input_hash_match"] is True
    assert report["result_hash_match"] is True
    assert report["trace_match"] is True
    assert report["result_original_matches"] is True
    assert report["chain"]["ok"] is True


def test_replay_does_not_write_new_events(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    before = len(app.events.all_events(run["run_id"]))
    app.orchestrator.replay(run["run_id"])
    app.orchestrator.replay(run["run_id"])
    assert len(app.events.all_events(run["run_id"])) == before


def test_replay_detects_modified_input(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    app.store.update_run(
        run["run_id"],
        status=run["status"],
        payload=app.orchestrator._dump({**run, "input_hash": "0" * 64}),
    )
    report = app.orchestrator.replay(run["run_id"])
    assert report["input_hash_match"] is False
    assert report["result_hash_match"] is True  # 结果本身仍可复现


def test_result_hash_ignores_generated_at(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    result = app.get_result(run["run_id"])
    mutated = {**result, "generated_at": "2030-01-01T00:00:00+08:00"}
    assert app.orchestrator._result_hash(result) == app.orchestrator._result_hash(mutated)
    mutated["status"] = "needs_review"
    assert app.orchestrator._result_hash(result) != app.orchestrator._result_hash(mutated)


def test_rerun_is_idempotent_for_result(app, pledge_document: str) -> None:
    first = app.run_task(_request([pledge_document]))
    second = app.run_task(_request([pledge_document]))
    assert first["run_id"] != second["run_id"]
    assert first["input_hash"] == second["input_hash"]
    assert first["result_hash"] == second["result_hash"]
    assert app.get_result(first["run_id"])["data"] == app.get_result(second["run_id"])["data"]


# ---------------------------------------------------------------- 人工复核


def test_review_decision_accept(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = app.run_task(_request([document["document_id"]], announcement_types=["pledge"]))
    result = app.get_result(run["run_id"])
    review_id = result["review_items"][0]["review_id"]

    item = app.orchestrator.decide_review(
        review_id,
        {"decision": "accept", "reviewer": "member_b", "note": "已核对原文，确实未披露", "corrected_value": None},
    )
    validate_contract("ReviewItem", item, label="ReviewItem")
    assert item["status"] == "accepted"
    assert item["target_id"] == result["review_items"][0]["target_id"]

    events = app.events.all_events(run["run_id"])
    assert any("accept" in event["message"] for event in events)


def test_review_decision_correct_writes_back_value(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = app.run_task(_request([document["document_id"]], announcement_types=["pledge"]))
    result = app.get_result(run["run_id"])
    item = result["review_items"][0]
    target = item["target_id"]

    updated = app.orchestrator.decide_review(
        item["review_id"],
        {"decision": "correct", "reviewer": "member_a", "note": "按补充公告补录股数", "corrected_value": "12000000"},
    )
    assert updated["status"] == "corrected"

    after = app.get_result(run["run_id"])
    record = next(record for record in after["data"]["records"] if record["event_id"] == target)
    fixed = [field for field in record["fields"] if field["status"] == "verified" and field["value"] == "12000000"]
    assert fixed, "人工修正的值必须写回对应记录字段"
    assert "人工复核修正" in (fixed[0]["reason"] or "")
    # 修正后重新计算 result_hash，Run 与结果保持一致
    run_row = app.get_run(run["run_id"])
    assert run_row["result_hash"] == app.orchestrator._result_hash(after)


def test_review_decision_rejects_bad_payload(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = app.run_task(_request([document["document_id"]], announcement_types=["pledge"]))
    review_id = app.get_result(run["run_id"])["review_items"][0]["review_id"]
    with pytest.raises(AppError) as error:
        app.orchestrator.decide_review(review_id, {"decision": "maybe", "reviewer": "x", "note": "y", "corrected_value": None})
    assert error.value.code == "VALIDATION_ERROR"


def test_review_of_unknown_id_raises_not_found(app) -> None:
    with pytest.raises(AppError) as error:
        app.orchestrator.decide_review(
            "review_missing", {"decision": "accept", "reviewer": "x", "note": "y", "corrected_value": None}
        )
    assert error.value.code == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------- 证据访问


def test_evidence_lookup_contract(app, pledge_document: str) -> None:
    run = app.run_task(_request([pledge_document]))
    result = app.get_result(run["run_id"])
    assert result["evidence_ids"]
    for evidence_id in result["evidence_ids"][:5]:
        evidence = app.get_evidence(evidence_id)
        validate_contract("Evidence", evidence, label="Evidence")
        assert evidence["document_id"] == pledge_document
        assert evidence["text"]


def test_missing_evidence_raises_not_found(app) -> None:
    with pytest.raises(AppError) as error:
        app.get_evidence("ev_not_exists")
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert error.value.http_status == 404


@pytest.mark.parametrize("failure", ["exception", "invalid_result"])
def test_plugin_failures_finish_run(app, pledge_document, failure):
    class BrokenPlugin:
        def run(self, context, task_input):
            if failure == "exception":
                raise RuntimeError("private-document-content")
            return {"data": {}, "evidence_ids": [123]}

    app.orchestrator.task_registry["structured_extraction"] = BrokenPlugin()
    run = app.run_task(_request([pledge_document]))
    validate_contract("Run", run, label="Run")
    assert run["status"] == "failed"
    assert run["finished_at"]
    assert run["error"]["code"] == ("INTERNAL_ERROR" if failure == "exception" else "VALIDATION_ERROR")
    directory = app.events.run_dir(run["run_id"])
    assert (directory / "error.json").exists()
    assert "private-document-content" not in (directory / "error.json").read_text(encoding="utf-8")
    assert "private-document-content" not in (directory / "events.jsonl").read_text(encoding="utf-8")
