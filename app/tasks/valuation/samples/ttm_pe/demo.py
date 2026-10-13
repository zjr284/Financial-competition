"""Synthetic data demonstration using the production financial calculator."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.tools import FinancialCalculator
from app.tasks.valuation import ValuationTask
from contracts.ports import TaskContext

HERE = Path(__file__).resolve().parent


class UnsupportedFormulaCalculator(FinancialCalculator):
    """Failure fixture only: simulate a deployment without the TTM formula."""
    def _evaluate(self, code, facts, calculations, params):
        if code == "ttm_from_ytd":
            raise ValueError("Unsupported formula_code: ttm_from_ytd")
        return super()._evaluate(code, facts, calculations, params)


def run_demo(fixture=None, calculator_type=FinancialCalculator):
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
    payload["warnings"].insert(0, "接口演示样例：全部数据虚构；使用生产 FinancialCalculator，真实财报资料仍需独立复核。")
    result = dict(api_version="1.0.0",run_id=ctx.run_id,task_type="valuation",status="needs_review",
                  generated_at="2026-10-13T12:00:00+08:00",input_document_ids=list(docs),**payload)
    return result


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--production-calculator",action="store_true",help="Compatibility alias: production is now the default");parser.add_argument("--needs-review",action="store_true")
    args=parser.parse_args()
    fixture=json.loads((HERE / "inputs.json").read_text())
    if args.needs_review:
        source=next(f for f in fixture["facts"] if f["fact_id"]=="fact_demo_999999_current")
        source["review_status"]="needs_review";source["reason"]="Synthetic pending review example"
    print(json.dumps(run_demo(fixture),ensure_ascii=False,indent=2))
