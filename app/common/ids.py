"""ID、哈希、时间与十进制字符串的统一工具。

契约要求（contracts.schema.json -> $defs.Id）：
  前缀必须是 doc/ev/fact/run/calc/review/event 之一，后接 [A-Za-z0-9_-]+
所有 ID 由内容派生，保证“同一 PDF 重复登记得到稳定 ID”。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

ID_PREFIXES = ("doc", "ev", "fact", "run", "calc", "review", "event")
ID_PATTERN = re.compile(r"^(doc|ev|fact|run|calc|review|event)_[A-Za-z0-9_-]+$")

# 时区固定为东八区，便于评审在本地复现
CN_TZ = timezone(__import__("datetime").timedelta(hours=8))

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """稳定序列化：用于计算 input_hash / result_hash / 事件哈希链。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest_of(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _base32_upper(data: bytes, length: int) -> str:
    """RFC4648 base32 的大写字母表（去掉填充），只用 [A-Z0-9]，天然满足 Id 正则。"""
    import base64

    encoded = base64.b32encode(data).decode("ascii").rstrip("=")
    return encoded[:length]


def make_id(prefix: str, *parts: Any, length: int = 20) -> str:
    """由内容派生稳定 ID。parts 相同 -> ID 相同。"""
    if prefix not in ID_PREFIXES:
        raise ValueError(f"非法 ID 前缀: {prefix}")
    seed = canonical_json(list(parts)).encode("utf-8")
    return f"{prefix}_{_base32_upper(hashlib.sha256(seed).digest(), length)}"


def new_id(prefix: str, length: int = 20) -> str:
    """随机 ID：仅用于 run/review 这类需要唯一性的对象。"""
    import uuid

    if prefix not in ID_PREFIXES:
        raise ValueError(f"非法 ID 前缀: {prefix}")
    return f"{prefix}_{_base32_upper(uuid.uuid4().bytes + uuid.uuid4().bytes, length)}"


def is_contract_id(value: object, prefix: str | None = None) -> bool:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        return False
    return prefix is None or value.startswith(f"{prefix}_")


def now_iso() -> str:
    """带时区的 ISO 8601（契约 $defs.DateTime，含秒以下精度）。"""
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------- 十进制字符串

DECIMAL_PATTERN = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


def dec_str(value: Decimal | int | str, *, places: int = 6, strip: bool = True) -> str:
    """转成契约要求的十进制字符串；不使用二进制浮点。

    金额/比例/倍数全部走这里，保证入库值可被 $defs.Decimal 校验。
    """
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    else:
        decimal_value = Decimal(str(value))
    if places >= 0:
        decimal_value = decimal_value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(decimal_value, "f")
    if strip and "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        text = "0"
    if not DECIMAL_PATTERN.match(text):
        raise ValueError(f"无法输出契约十进制字符串: {value!r} -> {text!r}")
    return text


def to_decimal(value: Any) -> Decimal | None:
    """把契约字符串或常规数字转成 Decimal；无法解析返回 None（禁止臆测值）。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):  # 二进制浮点只允许在边界处立刻转十进制字符串
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        if text == "":
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def normalize_bbox(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    width: float,
    height: float,
    *,
    scale: int = 1000,
) -> list[int]:
    """用户空间坐标 -> 契约 bbox。

    契约：左上角原点、0—1000 归一化整数、[x0,y0,x1,y1] 且 x0<x1, y0<y1。
    输入 y 使用 PDF 底原点坐标系，这里翻转为左上角原点。
    """
    if width <= 0 or height <= 0:
        return [0, 0, 0, 0]
    left = max(0.0, min(float(x0), float(x1)))
    right = min(float(width), max(float(x0), float(x1)))
    top_pdf = max(float(y0), float(y1))
    bottom_pdf = min(float(y0), float(y1))

    nx0 = int(round(left / width * scale))
    nx1 = int(round(right / width * scale))
    ny0 = int(round((height - top_pdf) / height * scale))
    ny1 = int(round((height - bottom_pdf) / height * scale))

    nx0 = max(0, min(scale, nx0))
    nx1 = max(0, min(scale, nx1))
    ny0 = max(0, min(scale, ny0))
    ny1 = max(0, min(scale, ny1))

    if nx0 >= nx1:
        nx1 = min(scale, nx0 + 1)
    if ny0 >= ny1:
        ny1 = min(scale, ny0 + 1)
    return [nx0, ny0, nx1, ny1]


def union_bbox(boxes: list[list[int]]) -> list[int]:
    """合并多个归一化 bbox；空列表返回全页占位。"""
    valid = [b for b in boxes if isinstance(b, list) and len(b) == 4]
    if not valid:
        return [0, 0, 0, 0]
    return [
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    ]


def truncate(text: str, limit: int = 400) -> str:
    """证据片段保持短原文，日志不写完整文档文本。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
