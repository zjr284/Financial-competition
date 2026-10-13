"""Synthetic integration adapter only; not registered with the production app."""
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.tools import FinancialCalculator
from app.tasks.valuation import ValuationTask
from contracts.ports import TaskContext

HERE = Path(__file__).resolve().parent


class DemoCalculator(FinancialCalculator):
    """Executable specification of B's requested A-port behavior on demo inputs."""
    def calculate(self, code, fact_ids, calc_ids, parameters):
        result = super().calculate(code, fact_ids, calc_ids, parameters)
        if code == "ttm_from_ytd":
            result["unit"] = "CNY"
            result["rounding_rule"] = "ROUND_HALF_UP:0.01"
        return result

    def _evaluate(self, code, facts, calculations, params):
        if any(f["review_status"] != "verified" or f["value"] is None for f in facts):
            raise ValueError("Demo input fact needs review")
        if any(c["status"] != "verified" or c["value"] is None for c in calculations):
            raise ValueError("Demo calculation needs review")
        if code == "ttm_from_ytd":
            self._require_counts(code, facts, calculations, 3, 0)
            return Decimal(facts[0]["value"]) + Decimal(facts[1]["value"]) - Decimal(facts[2]["value"])
        if code in {"pe", "relative_equity_value"} and calculations:
            self._require_counts(code, facts, calculations, 1 if code == "pe" else 0, 1)
            profit = Decimal(calculations[0]["value"])
            if profit <= 0:
                raise ValueError("Non-positive TTM earnings")
            if code == "pe":
                market = facts[0]
                if market["company_code"] != calculations[0]["parameters"]["company_code"]:
                    raise ValueError("Company mismatch")
                if market["period_end"] != params["as_of_date"] or Decimal(market["value"]) <= 0:
                    raise ValueError("Invalid market snapshot")
                return Decimal(market["value"]) / profit
            return profit * Decimal(params["multiple"]) * (1 + Decimal(params.get("earnings_shock_percent", "0")) / 100)
        return super()._evaluate(code, facts, calculations, params)


def run_demo(fixture=None, calculator_type=DemoCalculator):
    data = fixture or json.loads((HERE / "inputs.json").read_text())
    facts = data["facts"]; evidence = {e["evidence_id"]:e for e in data["evidence"]}
    docs = {d["document_id"]:d for d in data["documents"]}; events=[]
    port = SimpleNamespace(
        extract_facts=lambda ids, metrics: [f for f in facts if f["metric"] in metrics and
                                            all(evidence[e]["document_id"] in ids for e in f["evidence_ids"])],
        get_evidence=lambda eid:evidence[eid])
    ctx = TaskContext("run_demo_ttm_pe", tuple(docs), SimpleNamespace(get_document=lambda did:docs[did]),
                      port, calculator_type(), None, SimpleNamespace(append_event=lambda rid,event:events.append(event)))
    payload = ValuationTask().run(ctx, data["task_input"])
    payload["warnings"].insert(0, "接口演示样例：全部数据虚构；DemoCalculator 仅演示拟议工具行为，生产 A 工具尚未接入。")
    result = dict(api_version="1.0.0",run_id=ctx.run_id,task_type="valuation",status="needs_review",
                  generated_at="2026-10-12T12:00:00+08:00",input_document_ids=list(docs),**payload)
    return result


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--production-calculator",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run_demo(calculator_type=FinancialCalculator if args.production_calculator else DemoCalculator),ensure_ascii=False,indent=2))
