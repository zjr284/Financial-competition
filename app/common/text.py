"""中文字段值的确定性归一化。

结构化提取任务要求每个字段同时给出 raw_value（原文写法）与 value（归一值）+ unit。
本模块只做确定性换算，不做任何推测：
  * 全角转半角、去空白、去千分位
  * “万/亿/万亿”量纲换算为 base unit
  * 中文数字（含大写）转阿拉伯数字
  * 中文日期转 YYYY-MM-DD
解析失败一律返回 None，由调用方写成 value=null + reason，禁止填 0。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.common.ids import dec_str


# U+FF01—U+FF5E 是全角 ASCII，与半角相差 0xFEE0（含全角字母、数字、括号、
# 百分号、逗号、句点、连字符、冒号等）。此前只列了少数符号，导致“ａ”“１”
# 这类全角字符不被转换，标签匹配与金额解析都会漏。
FULLWIDTH_TABLE: dict[int, int | None] = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
FULLWIDTH_TABLE[ord("　")] = None  # 全角空格
FULLWIDTH_TABLE[ord("¥")] = ord("Y")
FULLWIDTH_TABLE[ord("￥")] = ord("Y")

_CN_DIGITS = {
    "零": 0, "〇": 0, "○": 0,
    "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5,
    "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9,
}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_SECTIONS = {"万": 10**4, "萬": 10**4, "亿": 10**8, "億": 10**8}

MAGNITUDE = {
    "": Decimal(1),
    "个": Decimal(1),
    "千": Decimal(10**3),
    "万": Decimal(10**4),
    "萬": Decimal(10**4),
    "十万": Decimal(10**5),
    "百万": Decimal(10**6),
    "千万": Decimal(10**7),
    "亿": Decimal(10**8),
    "億": Decimal(10**8),
    "万亿": Decimal(10**12),
}

# 币种前缀/后缀：只影响单位，不影响数量级
CURRENCY_WORDS = ("人民币", "RMB", "rmb", "CNY", "美元", "USD", "港元", "港币", "HKD", "欧元", "EUR", "日元", "JPY")

# 单位写法 -> 契约 $defs.Unit
UNIT_ALIASES: dict[str, str] = {
    "股": "share",
    "股数": "share",
    "万股": "share",
    "亿股": "share",
    "元": "CNY",
    "人民币元": "CNY",
    "万元": "CNY",
    "亿元": "CNY",
    "人民币万元": "CNY",
    "人民币亿元": "CNY",
    "%": "percent",
    "％": "percent",
    "个百分点": "percent",
    "倍": "times",
    "次": "count",
    "笔": "count",
    "项": "count",
    "家": "count",
    "吨": "ton",
    "天": "day",
    "日": "day",
    "月": "count",
    "年": "count",
}


def to_halfwidth(text: str) -> str:
    return text.translate(FULLWIDTH_TABLE)


def squeeze(text: str) -> str:
    """压缩内部空白：表格抽取经常在数字中间插入空格。"""
    text = to_halfwidth(text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def chinese_to_int(token: str) -> int | None:
    """把中文数字（含万/亿节权）转 int；非中文数字返回 None。"""
    if not token:
        return None
    if not any(ch in _CN_DIGITS or ch in _CN_UNITS or ch in _CN_SECTIONS for ch in token):
        return None
    total = 0
    section = 0
    number = 0
    for ch in token:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            section += (number or 1) * unit
            number = 0
        elif ch in _CN_SECTIONS:
            section += number
            total += section * _CN_SECTIONS[ch]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


_NUM_TOKEN = re.compile(r"^([+-]?)(\d+(?:\.\d+)?|\.\d+)$")


def _numeric_part(token: str) -> Decimal | None:
    token = token.strip()
    if token == "":
        return None
    match = _NUM_TOKEN.match(token)
    if match:
        sign, body = match.groups()
        value = Decimal(body)
        return -value if sign == "-" else value
    integer = chinese_to_int(token)
    if integer is None:
        return None
    return Decimal(integer)


def parse_quantity(raw: str, *, default_magnitude: str = "") -> tuple[Decimal | None, str, str]:
    """解析“数量 + 量纲 + 单位”。

    返回 (base_value, magnitude_text, unit_hint)。
      * base_value 已经乘上量纲，如 "1,000万股" -> (10000000, "万", "股")
      * 无法解析时 base_value=None
    """
    text = squeeze(raw)
    if text == "":
        return None, "", ""
    text = text.replace(",", "")
    # 去掉币种前缀/后缀，否则“人民币23,500万元”无法匹配数量+量纲+单位结构
    for word in CURRENCY_WORDS:
        text = text.replace(word, "")

    # 先尝试“数字 + 量纲 + 单位”结构，量纲最长 2 字
    pattern = re.compile(
        r"^(?P<num>[+-]?(?:\d+(?:\.\d+)?|\.\d+)|[零〇○一壹二贰两三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟万萬亿億]+)"
        r"\s*(?P<mag>万亿|千万|百万|十万|亿|億|万|萬|千)?\s*(?P<unit>[^\d\s]*)$"
    )
    match = pattern.match(text)
    if not match:
        return None, "", ""
    number = _numeric_part(match.group("num"))
    magnitude_text = match.group("mag") or default_magnitude
    unit_text = match.group("unit") or ""
    if number is None:
        return None, magnitude_text, unit_text
    factor = MAGNITUDE.get(magnitude_text)
    if factor is None:
        return None, magnitude_text, unit_text
    return number * factor, magnitude_text, unit_text


def scalar(raw: str) -> str | None:
    """解析金额/股数/计数为契约十进制字符串。"""
    value, _, _ = parse_quantity(raw)
    return None if value is None else dec_str(value)


def percent(raw: str) -> str | None:
    """解析百分数为“百分数值”字符串：'12.5%' -> '12.5'。

    契约明确 12.5 表示 12.5%，不是 0.125。
    """
    text = squeeze(raw).replace(",", "")
    match = re.search(r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))", text)
    if not match:
        return None
    return dec_str(Decimal(match.group(1)))


def unit_of(raw: str) -> str:
    """从原文推断契约 unit。"""
    text = squeeze(raw)
    for alias in sorted(UNIT_ALIASES, key=len, reverse=True):
        if alias and alias in text:
            return UNIT_ALIASES[alias]
    if "股" in text:
        return "share"
    if "元" in text:
        return "CNY"
    return "none"


_DATE_PATTERNS = [
    re.compile(r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"),
    re.compile(r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月"),
    re.compile(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"),
    re.compile(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})"),
]


def date(raw: str) -> str | None:
    """中文/常规日期写法定为 YYYY-MM-DD；无法确定到日返回 None。"""
    text = squeeze(raw)
    for index, pattern in enumerate(_DATE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        if index in (1, 3) and "d" not in groups:
            return None  # 只有年月，不能臆造日
        year, month, day = int(groups["y"]), int(groups["m"]), int(groups["d"])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def match_span(text: str, pattern: re.Pattern[str], group: int | str = 0) -> tuple[int, int] | None:
    """返回正则捕获组在 text 中的 (start, end)，用于从字符位置映射回 bbox。"""
    match = pattern.search(text)
    if not match:
        return None
    return match.span(group)


def clean_text(raw: str, limit: int = 200) -> str:
    text = squeeze(raw)
    return text[:limit]


def as_value_unit(raw: str, *, expect: str = "auto") -> tuple[str | None, str, str]:
    """把原文同时转成 (value, unit, reason)。

    expect: auto / amount / percent / share / date / text
    value 为 None 时 reason 说明原因，绝不返回 0 冒充。
    """
    if raw is None or squeeze(raw) == "":
        return None, "none", "原文未出现该字段"
    kind = expect
    if kind == "auto":
        text = squeeze(raw)
        if "%" in text or "％" in text:
            kind = "percent"
        elif "股" in text:
            kind = "share"
        elif "元" in text:
            kind = "amount"
        else:
            kind = "text"
    if kind == "percent":
        value = percent(raw)
        return (value, "percent", "" if value is not None else "百分比格式无法解析")
    if kind in {"amount", "share", "count"}:
        value = scalar(raw)
        unit = unit_of(raw) if kind == "amount" else ("share" if kind == "share" else "count")
        return (value, unit, "" if value is not None else "数值格式无法解析")
    if kind == "date":
        value = date(raw)
        return (value, "none", "" if value is not None else "日期格式无法解析到日")
    return (text_value(raw), "none", "")


def text_value(raw: str) -> str:
    return squeeze(raw)


def as_contract_unit(unit_hint: str, *, default: str = "none") -> str:
    return UNIT_ALIASES.get(squeeze(unit_hint), default)


def field_payload(
    field_code: str,
    raw: str | None,
    *,
    expect: str = "auto",
    evidence_ids: list[str] | None = None,
    status: str | None = None,
    reason: str | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    """按契约 FieldValue 组装一个字段。找不到值时保留字段并标 needs_review。"""
    from app.common.ids import truncate

    if raw is None:
        return {
            "field_code": field_code,
            "raw_value": None,
            "value": None,
            "unit": unit or "none",
            "evidence_ids": evidence_ids or [],
            "status": status or "needs_review",
            "reason": reason or "原文未出现该字段",
        }
    value, inferred_unit, parse_reason = as_value_unit(raw, expect=expect)
    final_unit = unit or inferred_unit
    if value is None:
        return {
            "field_code": field_code,
            "raw_value": truncate(raw, 120),
            "value": None,
            "unit": final_unit,
            "evidence_ids": evidence_ids or [],
            "status": "needs_review",
            "reason": reason or parse_reason or "无法归一化",
        }
    return {
        "field_code": field_code,
        "raw_value": truncate(raw, 120),
        "value": value,
        "unit": final_unit,
        "evidence_ids": evidence_ids or [],
        "status": status or "verified",
        "reason": reason,
    }
