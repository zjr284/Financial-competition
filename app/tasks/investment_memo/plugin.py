"""Evidence-linked investment memorandum candidate for analyst review."""

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


class InvestmentMemoTask:
    task_type = "investment_memo"

    def run(self, context: TaskContext, task_input: dict[str, Any]) -> dict[str, Any]:
        source_items = sources(context, company=task_input['company_code'],
                               as_of_date=task_input['as_of_date'], financial=True)
        known_ids = unique(
            evidence_id for item in source_items
            for evidence_id in item.get("evidence_ids", [])
        )
        if not known_ids:
            reason = "No verified company or industry evidence is available"
            return {
                "data": {
                    "thesis": [], "evidence": [], "risks": [],
                    "disconfirming_conditions": [], "monitoring_indicators": [],
                },
                "evidence_ids": [], "calculations": [],
                "review_items": [review_item(context.run_id, reason)],
                "warnings": [reason],
            }
        for evidence_id in known_ids:
            evidence = context.evidence.get_evidence(evidence_id)
            if evidence["document_id"] not in context.document_ids:
                raise ValueError(f"Evidence {evidence_id} is outside the run snapshot")
        data = context.model.infer_json(
            self.task_type, known_ids, "MemoData",
            {
                "company_code": task_input["company_code"],
                "as_of_date": task_input["as_of_date"],
                "source_items": source_items,
                "instructions": "Cite only archived evidence; separate fact, calculation, inference and opinion; include risks and disconfirming conditions.",
            },
        )
        validate_task_data("MemoData", data)
        statements = (
            data["thesis"] + data["evidence"] + data["risks"]
            + data["disconfirming_conditions"]
        )
        evidence_ids = source_ids_for_statements(context, statements, known_ids)
        reviews: list[dict[str, Any]] = []
        for statement in statements:
            if statement["kind"] in {"fact", "calculation"} and not statement["evidence_ids"]:
                reviews.append(review_item(context.run_id, "Memo factual statement lacks evidence"))
            if statement["kind"] in {"inference", "opinion"} and not (
                statement["assumptions"] or statement["limitations"]
            ):
                reviews.append(review_item(context.run_id, "Memo judgment lacks assumptions or limits"))
        if not data["risks"] or not data["disconfirming_conditions"] or not data["monitoring_indicators"]:
            reviews.append(review_item(context.run_id, "Memo lacks risk, disconfirming condition or tracking metric"))
        reviews.append(review_item(context.run_id, "Analyst must verify memo judgments before use", evidence_ids))
        return {
            "data": data,
            "evidence_ids": evidence_ids,
            "calculations": [],
            "review_items": reviews,
            "warnings": ["研究备忘录草稿须经人工审核，不构成投资建议"],
        }
