"""选题 1 结构化提取测试：成功样例、失败/待复核样例、归一化与证据挂接。

对应验收（`实施路线与验收清单.md` 第 4 节）：
随机抽金额、比例、日期与原件坐标核对；必填字段缺失必须显示空值和原因，不能编造。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.common.contracts import validate_contract
from app.tasks.structured_extraction.dictionary import ANNOUNCEMENT_TYPES, FIELD_SPECS

ALL_TYPES = list(ANNOUNCEMENT_TYPES)


def _run(app, document_id: str, types: list[str] | None = None) -> dict:
    run = app.run_task(
        {
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document_id],
            "task_input": {"announcement_types": types or ALL_TYPES},
        }
    )
    assert run["status"] in {"succeeded", "needs_review", "failed"}, run
    return run


def _fields(record: dict) -> dict[str, dict]:
    return {field["field_code"]: field for field in record["fields"]}


# ---------------------------------------------------------------- 成功样例


@pytest.mark.parametrize(
    ("filename", "announcement_type", "expected"),
    [
        (
            "announcement_pledge_demo.txt",
            "pledge",
            {
                "shareholder": None,
                "pledged_shares": "10000000",
                "pledged_ratio": "5.2",
                "pledge_start_date": "2026-08-20",
                "pledge_end_date": "2027-08-19",
                "pledgee": None,
            },
        ),
        (
            "announcement_bid_demo.txt",
            "bid_award",
            {
                "project_name": None,
                "winning_party": None,
                "contract_amount": "235000000",
                "currency": None,
                "performance_period": None,
            },
        ),
        (
            "announcement_equity_demo.txt",
            "equity_change",
            {
                "holder": None,
                "shares_before": "24000000",
                "shares_after": "20000000",
                "ratio_before": "12",
                "ratio_after": "10",
                "change_method": None,
            },
        ),
        (
            "announcement_pledge_demo_cjk.pdf",
            "pledge",
            {
                "shareholder": None,
                "pledged_shares": "10000000",
                "pledged_ratio": "5.2",
                "pledge_start_date": "2026-08-20",
                "pledge_end_date": "2027-08-19",
                "pledgee": None,
            },
        ),
    ],
)
def test_success_samples(app, samples_dir: Path, filename: str, announcement_type: str, expected: dict) -> None:
    document = app.upload_path(str(samples_dir / filename), disclosure_date="2026-09-01")
    run = _run(app, document["document_id"], [announcement_type])
    result = app.get_result(run["run_id"])

    assert result["status"] == "succeeded"
    assert result["task_type"] == "structured_extraction"
    validate_contract("TaskResult", result, label="TaskResult")

    records = result["data"]["records"]
    assert len(records) == 1, f"{filename} 应产出 1 条记录，实际 {len(records)}"
    record = records[0]
    assert record["announcement_type"] == announcement_type
    assert record["document_id"] == document["document_id"]
    assert record["company_code"] == "999999"
    assert record["event_date"]

    fields = _fields(record)
    # 契约字段字典 v1：每个公告类型必须出现全部字段
    for spec in FIELD_SPECS[announcement_type]:
        assert spec.field_code in fields, f"缺少字段 {spec.field_code}"

    for code, expected_value in expected.items():
        field = fields[code]
        assert field["status"] in {"verified", "needs_review"}
        if expected_value is None:
            # 文本类字段：只断言取到了非空值，具体文本由人工核对
            assert field["value"], f"{code} 应有值，实际 {field!r}"
        else:
            assert field["value"] == expected_value, f"{code} 归一化不符: {field!r}"

    # 每个已验证字段都必须挂证据；缺失字段必须给原因
    for field in record["fields"]:
        if field["status"] == "verified":
            if field["field_code"] == "document_id":
                continue  # 记录级字段，来源就是登记信息本身
            assert field["evidence_ids"], f"{field['field_code']} 无证据坐标"
        else:
            assert field["value"] is None
            assert field["reason"], f"{field['field_code']} 缺 reason"

    # result.evidence_ids 必须覆盖字段引用的全部证据
    assert set(result["evidence_ids"]) >= {
        evidence_id for field in record["fields"] for evidence_id in field["evidence_ids"]
    }


def test_units_follow_contract(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_bid_demo.txt"))
    run = _run(app, document["document_id"], ["bid_award"])
    fields = _fields(app.get_result(run["run_id"])["data"]["records"][0])
    assert fields["contract_amount"]["unit"] == "CNY"
    assert fields["contract_amount"]["value"] == "235000000"  # 23,500 万元 -> 元
    assert fields["contract_amount"]["raw_value"] == "23,500万元"  # 原值保留

    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = _run(app, document["document_id"], ["pledge"])
    fields = _fields(app.get_result(run["run_id"])["data"]["records"][0])
    assert fields["pledged_shares"]["unit"] == "share"
    # 契约：百分比是百分数数值，5.20% -> "5.2"，不是 0.052
    assert fields["pledged_ratio"]["unit"] == "percent"
    assert fields["pledged_ratio"]["value"] == "5.2"


def test_evidence_points_at_original_text(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo_cjk.pdf"))
    run = _run(app, document["document_id"], ["pledge"])
    result = app.get_result(run["run_id"])
    fields = _fields(result["data"]["records"][0])

    evidence = app.get_evidence(fields["pledged_shares"]["evidence_ids"][0])
    validate_contract("Evidence", evidence, label="Evidence")
    assert evidence["document_id"] == document["document_id"]
    assert evidence["page"] == 1
    assert evidence["extraction_method"] == "pdf_text"
    # 证据片段必须是原文子串（短片段带上下文），不是模型改写的句子
    assert "1,000万股" in evidence["text"]


# ---------------------------------------------------------------- 失败/待复核样例


def test_incomplete_sample_returns_needs_review_without_inventing_values(app, samples_dir: Path) -> None:
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = _run(app, document["document_id"], ["pledge"])
    result = app.get_result(run["run_id"])

    assert run["status"] == "needs_review"
    assert result["status"] == "needs_review"
    validate_contract("TaskResult", result, label="TaskResult")

    record = result["data"]["records"][0]
    fields = _fields(record)

    # 原文写的是“详见附件”：必须留空 + 说明，绝不能填 0
    for code in ("pledged_shares", "pledged_ratio", "pledge_start_date", "pledge_end_date", "pledgee"):
        assert fields[code]["value"] is None, f"{code} 不应有值"
        assert fields[code]["status"] == "needs_review"
        assert fields[code]["reason"]
        assert fields[code]["evidence_ids"] == [] or all(
            isinstance(item, str) for item in fields[code]["evidence_ids"]
        )

    # 出质人有明确原文，应正常抽取
    assert fields["shareholder"]["value"] == "示例控股集团有限公司"
    assert fields["shareholder"]["status"] == "verified"

    # 必须有复核项，且指向本记录
    assert result["review_items"], "待复核字段必须产生 review_items"
    for item in result["review_items"]:
        validate_contract("ReviewItem", item, label="ReviewItem")
        assert item["status"] == "pending"
        assert item["target_id"] == record["event_id"]
        assert item["reason"]


def test_explanatory_sentence_is_not_used_as_value(app, samples_dir: Path) -> None:
    """样例里“说明：本样例故意缺失质权人…”不得被当成质权人字段的值。"""
    document = app.upload_path(str(samples_dir / "announcement_pledge_incomplete_demo.txt"))
    run = _run(app, document["document_id"], ["pledge"])
    fields = _fields(app.get_result(run["run_id"])["data"]["records"][0])
    assert fields["pledgee"]["value"] is None
    assert "故意" not in str(fields["pledgee"]["raw_value"])


def test_no_target_announcement_produces_review_item(app, samples_dir: Path) -> None:
    """只找中标公告但资料是质押公告：不得返回空记录当作成功。"""
    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = _run(app, document["document_id"], ["bid_award"])
    result = app.get_result(run["run_id"])
    assert result["status"] == "needs_review"
    assert result["data"]["records"] == []
    assert result["review_items"]
    assert result["warnings"]


def test_unknown_announcement_type_rejected_by_contract(app, samples_dir: Path) -> None:
    from app.errors import AppError

    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    with pytest.raises(AppError) as error:
        app.run_task(
            {
                "api_version": "1.0.0",
                "task_type": "structured_extraction",
                "document_ids": [document["document_id"]],
                "task_input": {"announcement_types": ["pledge", "unknown_type"]},
            }
        )
    assert error.value.code == "VALIDATION_ERROR"


# ---------------------------------------------------------------- 表格路径


def test_csv_table_document_produces_row_records(app, samples_dir: Path) -> None:
    """表头用字段代码写的 CSV：每个数据行一条记录，字段按表头映射。"""
    document = app.upload_path(str(samples_dir / "pledge_table_demo.csv"))
    run = _run(app, document["document_id"], ["pledge"])
    result = app.get_result(run["run_id"])
    records = result["data"]["records"]
    assert len(records) == 2, f"两行数据应产出两条记录，实际 {len(records)}"

    first = _fields(records[0])
    assert first["pledged_shares"]["value"] == "10000000"
    assert first["pledged_ratio"]["value"] == "5.2"
    assert first["shareholder"]["value"] == "示例控股集团有限公司"
    assert records[0]["event_date"] == "2026-08-20"

    second = _fields(records[1])
    assert second["pledged_shares"]["value"] == "5000000"
    assert second["pledged_ratio"]["value"] == "2.6"
    assert records[1]["event_date"] == "2026-09-01"
    assert records[0]["event_id"] != records[1]["event_id"]

    # 表格证据必须带 row/column，行列从 1 开始
    evidence = app.get_evidence(first["pledged_shares"]["evidence_ids"][0])
    assert evidence["extraction_method"] == "table"
    assert evidence["row"] is not None and evidence["row"] >= 1
    assert evidence["column"] is not None and evidence["column"] >= 1


def test_record_level_fields_are_fixed(app, samples_dir: Path) -> None:
    """字段字典规定提取记录固定包含 document_id, company_code, event_date。"""
    document = app.upload_path(str(samples_dir / "announcement_bid_demo.txt"))
    run = _run(app, document["document_id"], ["bid_award"])
    record = app.get_result(run["run_id"])["data"]["records"][0]
    codes = [field["field_code"] for field in record["fields"]]
    for required in ("document_id", "company_code", "event_date"):
        assert required in codes
    assert record["company_code"] == "999999"


def test_event_id_satisfies_contract_id_pattern(app, samples_dir: Path) -> None:
    """记录 event_id 必须在契约 Id 家族内，否则复核项无法引用它。"""
    import re

    document = app.upload_path(str(samples_dir / "announcement_pledge_demo.txt"))
    run = _run(app, document["document_id"], ["pledge"])
    result = app.get_result(run["run_id"])
    pattern = re.compile(r"^(doc|ev|fact|run|calc|review|event)_[A-Za-z0-9_-]+$")
    for record in result["data"]["records"]:
        assert pattern.match(record["event_id"]), record["event_id"]
