"""仅供 C 模块离线合成测试。不得注册到 A 的生产服务。"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from app.tasks.research_verification.support import stable_id
from contracts.ports import TaskContext


class FixturePorts:
    def __init__(self, package):
        self.package = package
        self.documents = {d["document_id"]: d for d in package["documents"]}
        self.evidence = {e["evidence_id"]: e for e in package["evidence"]}
        self.facts = {f["fact_id"]: f for f in package["facts"]}
        self.events = []

    def get_document(self, document_id):
        return self.documents[document_id]

    def parse_document(self, document_id):
        return {"evidence_ids": [eid for eid, ev in self.evidence.items() if ev["document_id"] == document_id],
                "warnings": ["合成接口演示资料；非上市公司披露或竞赛实测"]}

    def get_evidence(self, evidence_id):
        return self.evidence[evidence_id]

    def extract_facts(self, document_ids, field_codes):
        return [f for f in self.facts.values()
                if (not field_codes or f["metric"] in field_codes)
                and all(self.evidence[eid]["document_id"] in document_ids for eid in f["evidence_ids"])]

    def calculate(self, formula_code, input_fact_ids, input_calculation_ids, parameters):
        if formula_code != "yoy" or len(input_fact_ids) != 2 or input_calculation_ids:
            raise ValueError("演示工具仅支持两个 Fact 的同比；生产计算由 B 提供")
        current, prior = [self.facts[fid] for fid in input_fact_ids]
        value, reason = None, None
        same_scope = current["statement_scope"] == prior["statement_scope"]
        same_period = (current["period_start"][5:] == prior["period_start"][5:]
                       and current["period_end"][5:] == prior["period_end"][5:]
                       and current["period_type"] == prior["period_type"])
        if not same_scope or not same_period or Decimal(prior["value"]) <= 0:
            reason = "演示计算：基期无效或期间/口径不一致"
        else:
            value = format(((Decimal(current["value"]) / Decimal(prior["value"]) - 1) * 100)
                           .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")
        return {"calculation_id": stable_id("calc", [formula_code, input_fact_ids]),
                "formula_code": formula_code, "input_fact_ids": input_fact_ids,
                "input_calculation_ids": [], "parameters": parameters, "value": value,
                "unit": "percent", "rounding_rule": "half_up_4_decimal_places",
                "status": "verified" if value is not None else "needs_review", "reason": reason}

    def infer_json(self, *args, **kwargs):
        raise RuntimeError("合成测试未接入模型；C 规则模块不会声称使用了大模型")

    def append_event(self, run_id, event):
        self.events.append(event)

    def context(self, run_id="run_c_demo", document_ids=None):
        return TaskContext(run_id, tuple(document_ids or self.documents), self, self, self, self, self)


def load_package(path=None):
    path = path or Path(__file__).parent / "fixtures" / "package.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))
