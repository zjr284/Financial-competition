"""Evidence-linked investment memorandum candidate for analyst review."""

from __future__ import annotations

from typing import Any
from copy import deepcopy

from app.tasks._finance_common import (
    review_item,
    source_ids_for_statements,
    unique,
    validate_task_data,
)
from contracts.ports import TaskContext
from app.tasks.synthesis_sources import sources
from app.tasks.investment_memo.local_facts import financial_appendix


class InvestmentMemoTask:
    task_type = "investment_memo"

    def run(self, context: TaskContext, task_input: dict[str, Any]) -> dict[str, Any]:
        local_statements, financial_reviews = financial_appendix(
            context, task_input['company_code'], task_input['as_of_date'])
        source_items = sources(context, company=task_input['company_code'],
                               as_of_date=task_input['as_of_date'], financial=False)
        known_ids = unique(
            evidence_id for item in source_items
            for evidence_id in item.get("evidence_ids", [])
        )
        if not known_ids:
            reason = "No eligible qualitative excerpts; memo contains only available local financial facts"
            local_ids = unique(eid for item in local_statements for eid in item['evidence_ids'])
            return {
                "data": {
                    "thesis": [], "evidence": local_statements, "risks": [],
                    "disconfirming_conditions": [], "monitoring_indicators": [],
                },
                "evidence_ids": local_ids, "calculations": [],
                "review_items": financial_reviews + [review_item(context.run_id, reason, local_ids)],
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
                "instructions_version": "memo-qualitative-local-financial-v7",
                "instructions": (
                    "Only qualitative excerpts are supplied. Do not invent financial amounts, ratios or "
                    "growth rates; verified financial facts are appended locally after your response. "
                    "Cite only archived evidence; separate fact, calculation, inference and opinion; "
                    "include risks and disconfirming conditions. Attribute disclosed statements to their source. "
                    "Preserve the company, product and value-chain stage to which each source claim applies. "
                    "Do not transfer a customer's or industry's component costs, margin exposure or inventory "
                    "to the focus company without explicit source support. Where evidence concerns customer "
                    "product costs, discuss indirect demand transmission as a conditional inference; "
                    "state that company cost, pricing and margin effects require additional evidence. "
                    "Do not assume the focus company procures, produces or sells those components. "
                    "Every statement, including a risk or disconfirming condition, must cite a supplied "
                    "excerpt that motivates it. Stay within the products and relationships in those "
                    "excerpts; do not introduce other business segments from background knowledge. "
                    "For customer component cost pressure, limit conclusions to conditional changes "
                    "in customer demand or bargaining pressure; do not say the focus company's own "
                    "product pricing must pass through that component cost. Keep the draft concise."
                    " Distinguish reporting periods; disclosure_date is publication date, not the reporting "
                    "period. Attribute management self-assessments to the report, and do not treat an older "
                    "strategy statement as proof of later execution."
                    " Respond in Chinese. Explicitly attribute safety/reliability improvement claims "
                    "to the company report; these are not independently verified. Overseas coverage of "
                    "family/commercial storage describes only that segment, not the whole storage business "
                    "or domestic business shares. Core regions do not establish principal overseas "
                    "revenue sources without regional revenue data, even as an assumption."
                    " If a statement spans consecutive excerpts, cite every supporting continuation "
                    "on that statement itself. A page header does not support a substantive claim. "
                    "If the needed continuation is not supplied, shorten the claim to the cited text."
                ),
            },
        )
        data = deepcopy(data)
        validate_task_data("MemoData", data)
        statements = (
            data["thesis"] + data["evidence"] + data["risks"]
            + data["disconfirming_conditions"]
        )
        evidence_ids = source_ids_for_statements(context, statements, known_ids)
        reviews: list[dict[str, Any]] = list(financial_reviews)
        for statement in statements:
            if not statement["evidence_ids"]:
                reviews.append(review_item(context.run_id, "Memo statement lacks source evidence"))
            if statement["kind"] in {"inference", "opinion"} and not (
                statement["assumptions"] or statement["limitations"]
            ):
                reviews.append(review_item(context.run_id, "Memo judgment lacks assumptions or limits"))
        if not data["risks"] or not data["disconfirming_conditions"] or not data["monitoring_indicators"]:
            reviews.append(review_item(context.run_id, "Memo lacks risk, disconfirming condition or tracking metric"))
        # Model citations were checked against qualitative inputs before adding local facts.
        data['evidence'].extend(local_statements)
        evidence_ids = unique(evidence_ids + [eid for item in local_statements for eid in item['evidence_ids']])
        validate_task_data('MemoData', data)
        reviews.append(review_item(context.run_id, "Analyst must verify memo judgments before use", evidence_ids))
        return {
            "data": data,
            "evidence_ids": evidence_ids,
            "calculations": [],
            "review_items": reviews,
            "warnings": ["研究备忘录草稿须经人工审核，不构成投资建议"],
        }
