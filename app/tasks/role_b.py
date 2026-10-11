"""Role B task registry for the orchestration layer."""

from __future__ import annotations

from app.tasks.financial_analysis import FinancialAnalysisTask
from app.tasks.industry_impact import IndustryImpactTask
from app.tasks.investment_memo import InvestmentMemoTask
from app.tasks.valuation import ValuationTask


def get_role_b_tasks() -> dict[str, object]:
    plugins = (
        FinancialAnalysisTask(),
        IndustryImpactTask(),
        ValuationTask(),
        InvestmentMemoTask(),
    )
    return {plugin.task_type: plugin for plugin in plugins}
