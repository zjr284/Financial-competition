"""归一化工具测试：中文数字、量纲、百分比、日期与十进制字符串契约。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.common.ids import dec_str, digest_of, make_id, normalize_bbox, to_decimal, union_bbox
from app.common.text import (
    as_value_unit,
    chinese_to_int,
    date,
    field_payload,
    parse_quantity,
    percent,
    scalar,
    squeeze,
    to_halfwidth,
)


def test_chinese_numbers() -> None:
    assert chinese_to_int("十") == 10
    assert chinese_to_int("十二") == 12
    assert chinese_to_int("三十二") == 32
    assert chinese_to_int("一千零二十") == 1020
    assert chinese_to_int("两千五百万") == 25000000
    assert chinese_to_int("壹佰贰拾") == 120
    assert chinese_to_int("abc") is None


def test_quantity_magnitude() -> None:
    value, magnitude, unit = parse_quantity("1,000万股")
    assert value == Decimal(10000000)
    assert magnitude == "万"
    assert unit == "股"

    value, magnitude, _ = parse_quantity("1.5亿元")
    assert value == Decimal("150000000")
    assert magnitude == "亿"

    value, _, _ = parse_quantity("23,500万元")
    assert value == Decimal("235000000")

    assert parse_quantity("详见附件")[0] is None


def test_scalar_outputs_contract_decimal_string() -> None:
    assert scalar("1,000万股") == "10000000"
    assert scalar("2.60") == "2.6"
    assert scalar("人民币23,500万元") == "235000000"
    assert scalar("详见附件") is None


def test_percent_is_percentage_points() -> None:
    # 契约：12.5 表示 12.5%，不是 0.125
    assert percent("5.20%") == "5.2"
    assert percent("12.00％") == "12"
    assert percent("无") is None


def test_dates() -> None:
    assert date("2026年8月20日") == "2026-08-20"
    assert date("2026-09-01") == "2026-09-01"
    assert date("2026/9/1") == "2026-09-01"
    assert date("2026年8月") is None  # 不能臆造“日”
    assert date("13月40日") is None


def test_field_payload_never_invents_zero() -> None:
    missing = field_payload("pledged_shares", None, expect="share")
    assert missing["value"] is None
    assert missing["status"] == "needs_review"
    assert missing["reason"]

    unparsable = field_payload("pledged_shares", "详见附件", expect="share")
    assert unparsable["value"] is None
    assert unparsable["status"] == "needs_review"

    good = field_payload("pledged_shares", "1,000万股", expect="share", evidence_ids=["ev_x"])
    assert good["value"] == "10000000"
    assert good["unit"] == "share"
    assert good["status"] == "verified"


def test_fullwidth_and_squeeze() -> None:
    assert to_halfwidth("１２３（ａ）") == "123(a)"
    assert squeeze("营业收入   １２３ 元") == "营业收入 123 元"


def test_as_value_unit_auto_detect() -> None:
    value, unit, reason = as_value_unit("5.20%")
    assert (value, unit) == ("5.2", "percent")
    assert reason == ""
    value, unit, _ = as_value_unit("1,000万股")
    assert (value, unit) == ("10000000", "share")


def test_decimal_strings_reject_bad_shapes() -> None:
    assert dec_str(Decimal("1.500000")) == "1.5"
    assert dec_str(Decimal("0.10")) == "0.1"
    with pytest.raises(ValueError):
        dec_str(Decimal("NaN"))
    assert to_decimal("1,234.5") == Decimal("1234.5")
    assert to_decimal("not a number") is None


def test_bbox_normalization_flips_y_and_orders() -> None:
    # PDF 底原点：页面顶部（y 大）应映射到归一化后的小 y
    top = normalize_bbox(56, 777, 300, 789, 595, 842)
    bottom = normalize_bbox(56, 60, 300, 72, 595, 842)
    assert top[0] < top[2] and top[1] < top[3]
    assert 0 <= top[1] < 100, f"顶部内容应落在归一化小 y：{top}"
    assert bottom[1] > 800, f"底部内容应落在归一化大 y：{bottom}"
    assert top[1] < bottom[1]
    # 页面尺寸非法时不给伪造坐标，返回占位
    assert normalize_bbox(1, 2, 3, 4, 0, 0) == [0, 0, 0, 0]
    assert union_bbox([[10, 20, 30, 40], [50, 5, 60, 45]]) == [10, 5, 60, 45]


def test_ids_are_stable_and_contract_shaped() -> None:
    first = make_id("ev", "doc_a", 1, [0, 1, 2, 3], "文本")
    second = make_id("ev", "doc_a", 1, [0, 1, 2, 3], "文本")
    assert first == second
    assert first.startswith("ev_")
    assert make_id("ev", "doc_a", 1, [0, 1, 2, 4], "文本") != first
    with pytest.raises(ValueError):
        make_id("bad", "x")


def test_digest_is_order_insensitive_for_dicts() -> None:
    assert digest_of({"a": 1, "b": [2]}) == digest_of({"b": [2], "a": 1})
    assert digest_of({"a": 1}) != digest_of({"a": 2})
