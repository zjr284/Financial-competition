"""契约加载与校验：contracts/contracts.schema.json 是唯一事实来源。

所有跨模块传递的 JSON（Document、Evidence、Run、TaskResult、Event…）在写出前
必须经过 validate_contract；校验失败按 VALIDATION_ERROR 抛出，不静默降级。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema

from app.config import REPO_ROOT
from app.errors import validation_error


SCHEMA_PATH = REPO_ROOT / "contracts" / "contracts.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(definition: str) -> jsonschema.Draft202012Validator:
    defs = schema()["$defs"]
    if definition not in defs:
        raise KeyError(f"契约中不存在定义: {definition}")
    return jsonschema.Draft202012Validator(
        {"$defs": defs, "$ref": f"#/$defs/{definition}"},
        format_checker=jsonschema.FormatChecker(),
    )


def errors_for(definition: str, value: object) -> list[str]:
    """返回人类可读的校验错误列表（最多 5 条）。"""
    found = sorted(_validator(definition).iter_errors(value), key=lambda err: list(map(str, err.path)))
    return [f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in found[:5]]


def validate_contract(definition: str, value: object, *, label: str | None = None) -> None:
    """校验失败抛 VALIDATION_ERROR，details 带上契约定义名与首个错误路径。"""
    found = errors_for(definition, value)
    if found:
        raise validation_error(
            f"{label or definition} 不符合契约 {definition}：{found[0]}",
            definition=definition,
            label=label or definition,
            errors=found,
        )


def is_valid(definition: str, value: object) -> bool:
    return _validator(definition).is_valid(value)


def required_fields(definition: str) -> tuple[str, ...]:
    return tuple(schema()["$defs"][definition].get("required", ()))


def id_prefixes() -> tuple[str, ...]:
    pattern = schema()["$defs"]["Id"]["pattern"]
    inner = pattern.removeprefix("^(").split(")", 1)[0]
    return tuple(inner.split("|"))


def units() -> tuple[str, ...]:
    return tuple(schema()["$defs"]["Unit"]["enum"])


def task_types() -> tuple[str, ...]:
    return tuple(schema()["$defs"]["TaskType"]["enum"])


def build_shell(definition: str) -> dict[str, Any]:
    """按契约必填字段生成一个骨架对象，便于构造错误样例与前端占位。

    数值一律给 None（缺失值契约要求 null + reason），绝不填 0 冒充。
    """
    properties = schema()["$defs"][definition]["properties"]
    shell: dict[str, Any] = {}
    for name in required_fields(definition):
        prop = properties.get(name, {})
        if "oneOf" in prop:
            shell[name] = None
        elif "type" in prop and isinstance(prop["type"], str):
            shell[name] = {
                "string": "",
                "integer": 0,
                "number": 0,
                "boolean": False,
                "array": [],
                "object": {},
                "null": None,
            }[prop["type"]]
        elif "enum" in prop:
            shell[name] = prop["enum"][0]
        elif "$ref" in prop:
            shell[name] = None
        else:
            shell[name] = None
    return shell


def schema_dir() -> Path:
    return SCHEMA_PATH.parent
