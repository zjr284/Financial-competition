"""Grounded industry transmission analysis through the model port."""

from __future__ import annotations

from typing import Any

from app.tasks._finance_common import (
    review_item,
    source_ids_for_statements,
    unique,
    validate_task_data,
)
from contracts.ports import TaskContext
from app.tasks.synthesis_sources import sources


class IndustryImpactTask:
    task_type = "industry_impact"

    def run(self, context: TaskContext, task_input: dict[str, Any]) -> dict[str, Any]:
        source_items = sources(context, company=task_input['focus_company_code'])
        known_ids = unique(
            evidence_id
            for item in source_items
            for evidence_id in item.get("evidence_ids", [])
        )
        if not known_ids:
            reason = "No archived industry-event evidence is available"
            return {
                "data": {"nodes": [], "edges": [], "scenarios": [], "conclusions": []},
                "evidence_ids": [], "calculations": [],
                "review_items": [review_item(context.run_id, reason)],
                "warnings": [reason],
            }
        for evidence_id in known_ids:
            evidence = context.evidence.get_evidence(evidence_id)
            if evidence["document_id"] not in context.document_ids:
                raise ValueError(f"Evidence {evidence_id} is outside the run snapshot")
        data = context.model.infer_json(
            self.task_type, known_ids, "IndustryData",
            {
                "focus_company_code": task_input["focus_company_code"],
                "event_description": task_input["event_description"],
                "scenario_ids": task_input["scenario_ids"],
                "source_items": source_items,
                "instructions_version": "industry-scoped-sources-v2",
                "instructions": (
                    "Use only archived evidence IDs; distinguish observed facts from hypothetical transmission paths. "
                    "Respect the company and product scope of each excerpt. Attribute management self-assessments "
                    "to their report, not to independent verification. disclosure_date is publication date, "
                    "not the reporting period. An older strategic intention does not prove later execution."
                ),
            },
        )
        validate_task_data("IndustryData", data)
        node_ids = {node["node_id"] for node in data["nodes"]}
        reviews: list[dict[str, Any]] = []
        for edge in data["edges"]:
            if edge["from_id"] not in node_ids or edge["to_id"] not in node_ids:
                raise ValueError("Industry edge references an unknown node")
            if not edge["evidence_ids"]:
                reviews.append(review_item(context.run_id, "Industry edge lacks source evidence"))
        found_scenarios = {scenario["scenario_id"] for scenario in data["scenarios"]}
        for scenario_id in task_input["scenario_ids"]:
            if scenario_id not in found_scenarios:
                reviews.append(review_item(context.run_id, f"Missing scenario {scenario_id}"))
        statements = data["conclusions"] + data["edges"] + data["scenarios"]
        cited_ids = source_ids_for_statements(context, statements, known_ids)
        for conclusion in data["conclusions"]:
            if conclusion["kind"] in {"fact", "calculation"} and not conclusion["evidence_ids"]:
                reviews.append(review_item(context.run_id, "Industry conclusion claims fact without evidence"))
            if conclusion["kind"] in {"inference", "opinion"} and not (
                conclusion["assumptions"] or conclusion["limitations"]
            ):
                reviews.append(review_item(context.run_id, "Industry inference lacks assumptions or limits"))
        reviews.append(review_item(context.run_id, "Review causal direction and quantitative magnitude", cited_ids))
        return {
            "data": data,
            "evidence_ids": cited_ids,
            "calculations": [],
            "review_items": reviews,
            "warnings": ["产业链传导为条件性分析，需人工核对适用边界；原文片段登记不等于已核实金融事实"],
        }
