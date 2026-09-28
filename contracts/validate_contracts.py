"""Validate the public schemas, OpenAPI references, and synthetic examples."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parent
SCHEMA = json.loads((ROOT / "contracts.schema.json").read_text(encoding="utf-8"))
EXAMPLES = ROOT / "examples"
TASKS = {
    "structured_extraction",
    "financial_analysis",
    "industry_impact",
    "valuation",
    "research_verification",
    "investment_memo",
    "report_quality",
}


def validate(definition: str, value: object, label: str) -> None:
    validator = jsonschema.Draft202012Validator(
        {"$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{definition}"},
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(value), key=lambda err: list(map(str, err.path)))
    if errors:
        error = errors[0]
        raise ValueError(f"{label}: {'.'.join(map(str, error.path))}: {error.message}")


def check_openapi() -> None:
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["version"] == "1.0.0"
    assert len(spec["paths"]) == 10

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                if ref.startswith("./contracts.schema.json#/$defs/"):
                    name = ref.rsplit("/", 1)[-1]
                    assert name in SCHEMA["$defs"], f"Missing schema definition: {name}"
                elif ref.startswith("#/components/"):
                    parts = ref.removeprefix("#/components/").split("/")
                    target = spec["components"]
                    for part in parts:
                        target = target[part]
                else:
                    raise ValueError(f"Unsupported OpenAPI reference: {ref}")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(spec)


def main() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    check_openapi()
    requests = json.loads((EXAMPLES / "requests.json").read_text(encoding="utf-8"))
    results = json.loads((EXAMPLES / "results.json").read_text(encoding="utf-8"))
    assert {item["task_type"] for item in requests} == TASKS
    assert {item["task_type"] for item in results} == TASKS
    assert len(requests) == len(results) == len(TASKS)
    for index, item in enumerate(requests, 1):
        validate("RunRequest", item, f"request {index}")
    for index, item in enumerate(results, 1):
        validate("TaskResult", item, f"result {index}")
    entities = json.loads((EXAMPLES / "entities.json").read_text(encoding="utf-8"))
    for key, definition in {
        "document": "Document",
        "evidence": "Evidence",
        "run": "Run",
        "event_page": "EventPage",
        "review_decision": "ReviewDecision",
        "review_item": "ReviewItem",
        "health": "Health",
    }.items():
        validate(definition, entities[key], key)
    bbox = entities["evidence"]["bbox"]
    assert bbox[0] < bbox[2] and bbox[1] < bbox[3], "Invalid bbox ordering"
    quality = next(item for item in results if item["task_type"] == "report_quality")
    assert sum(row["weight"] for row in quality["data"]["rubric"]) == 100
    print("Contract validation passed: 7 requests, 7 results, 7 entities, 10 API paths")


if __name__ == "__main__":
    main()
