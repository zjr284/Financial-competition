from __future__ import annotations

import re
from decimal import Decimal

from .support import call, finish, load_evidence, review, stable_id, validate

# 规则只覆盖明确的、单指标、单数值声明。未知措辞保留待复核。
METRICS = {
    "营业收入": "revenue", "归母净利润": "net_profit_parent",
    "扣非归母净利润": "net_profit_parent_excl_nonrecurring",
    "经营现金流净额": "operating_cash_flow", "总资产": "total_assets",
    "总负债": "total_liabilities", "应收账款": "accounts_receivable", "存货": "inventory",
}
METRIC_RE = re.compile("|".join(sorted(METRICS, key=len, reverse=True)))
NUMBER_RE = re.compile(r"(?P<number>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元|元|%|％|倍)")
PERIOD_RE = re.compile(r"(?P<year>20\d{2})年(?P<part>全年|上半年|前三季度|第一季度|第二季度|第三季度|第四季度|末)")
SCALE = {"元": Decimal(1), "万元": Decimal(10000), "亿元": Decimal(100000000),
         "%": Decimal(1), "％": Decimal(1), "倍": Decimal(1)}
UNITS = {"元": "CNY", "万元": "CNY", "亿元": "CNY", "%": "percent", "％": "percent", "倍": "times"}
INSTANT = {"total_assets", "total_liabilities", "accounts_receivable", "inventory"}


def parse_claim(sentence):
    metrics = list(METRIC_RE.finditer(sentence))
    values = list(NUMBER_RE.finditer(sentence))
    periods = list(PERIOD_RE.finditer(sentence))
    if len(metrics) != 1 or len(values) != 1 or len(periods) != 1:
        return None
    # 仅放行肯定事实句模板，避免将否定、范围、比较或解释句判成肯定披露。
    remaining = sentence
    for match in sorted([metrics[0], values[0], periods[0]], key=lambda m: m.start(), reverse=True):
        remaining = remaining[:match.start()] + remaining[match.end():]
    remaining = re.sub(r"合并|母公司|口径|同比(?:增长|下降|减少|下滑|上升|变动)|为|是|达到|实现|达|\s", "", remaining)
    if remaining:
        return None
    # 非确定数值、预测、否定、条件句均不按披露事实判定。
    if re.search(r"预计|预测|或|约|可能|有望|至少|超过|不超过|低于|高于|不为|不是|并非|若|如果|假设|目标", sentence):
        return None
    if ("合并" in sentence) == ("母公司" in sentence):
        return None
    metric = METRICS[metrics[0].group()]
    period = periods[0]
    year, part = int(period["year"]), period["part"]
    if metric in INSTANT:
        if part != "末":
            return None
        start = end = f"{year}-12-31"
        period_type = "instant"
    else:
        dates = {"全年": ("01-01", "12-31", "cumulative"),
                 "上半年": ("01-01", "06-30", "cumulative"),
                 "前三季度": ("01-01", "09-30", "cumulative"),
                 "第一季度": ("01-01", "03-31", "single_quarter"),
                 "第二季度": ("04-01", "06-30", "single_quarter"),
                 "第三季度": ("07-01", "09-30", "single_quarter"),
                 "第四季度": ("10-01", "12-31", "single_quarter")}
        if part not in dates:
            return None
        a, b, period_type = dates[part]
        start, end = f"{year}-{a}", f"{year}-{b}"
    value = values[0]
    number, label = value["number"], value["unit"]
    formula = "yoy" if "同比" in sentence else None
    if formula and UNITS[label] != "percent":
        return None
    if not formula and UNITS[label] != "CNY":
        return None
    # 非同比百分比、倍数等复杂口径留给 B 的明确工具/人工复核。
    declared = Decimal(number)
    if formula and re.search(r"同比(?:下降|减少|下滑)", sentence):
        if declared < 0:
            return None  # 双重负号/含混表达交人工复核。
        declared = -declared
    return {"metric": metric, "value": declared * SCALE[label],
            "quantum": Decimal(1).scaleb(Decimal(number).as_tuple().exponent) * SCALE[label],
            "unit": UNITS[label], "label": label, "number": number,
            "start": start, "end": end, "period_type": period_type,
            "scope": "consolidated" if "合并" in sentence else "parent", "formula": formula}


def matches(fact, claim):
    return (fact["period_start"] == claim["start"] and fact["period_end"] == claim["end"]
            and fact["period_type"] == claim["period_type"] and fact["statement_scope"] == claim["scope"])


class ResearchVerificationTask:
    task_type = "research_verification"

    def run(self, context, task_input):
        validate("VerificationInput", task_input)
        draft_id = task_input["draft_document_id"]
        if draft_id not in context.document_ids:
            raise ValueError("草稿必须属于本次固定输入资料包")
        draft, warnings = load_evidence(context, draft_id)
        sources = {}
        for doc_id in context.document_ids:
            if doc_id != draft_id:
                evidence, notes = load_evidence(context, doc_id)
                sources.update((ev["evidence_id"], ev) for ev in evidence)
                warnings.extend(notes)
        facts = call(context, "tool_call", "extract_facts", list(context.document_ids),
                     lambda: context.evidence.extract_facts(
                         [doc for doc in context.document_ids if doc != draft_id], list(METRICS.values())))
        usable = []
        for fact in facts:
            validate("Fact", fact)
            if (fact["company_code"] == task_input["company_code"] and fact["review_status"] == "verified"
                    and fact["value"] is not None and all(eid in sources for eid in fact["evidence_ids"])
                    and all(sources[eid]["confidence"] >= 0.9 for eid in fact["evidence_ids"])
                    and all(sources[eid]["document_id"] != draft_id for eid in fact["evidence_ids"])):
                usable.append(fact)
        # B 的 FinancialCalculator 支持每次运行登记；纯 Protocol 适配器可预装事实。
        register = getattr(context.calculations, "register_facts", None)
        if callable(register):
            call(context, "tool_call", "register_facts", usable, lambda: register(usable))
        checks, calculations, reviews, used = [], [], [], []
        for ev in draft:
            for index, sentence in enumerate(filter(None, (s.strip() for s in re.split(r"[。！？；\n]+", ev["text"])))):
                claim_id = stable_id("claim", [draft_id, ev["evidence_id"], index, sentence])
                check = {"claim_id": claim_id, "sentence": sentence,
                         "draft_evidence_id": ev["evidence_id"], "decision": "needs_review",
                         "error_type": "insufficient_evidence", "severity": "medium",
                         "source_evidence_ids": [], "calculation_id": None, "suggested_edit": None,
                         "reason": "缺少明确的单指标、数值、期间或报表口径；语义/引用/估值声明需复核"}
                claim = parse_claim(sentence)
                if claim and ev["confidence"] >= 0.9:
                    candidates = [f for f in usable if f["metric"] == claim["metric"] and f["unit"] == "CNY"]
                    exact = [f for f in candidates if matches(f, claim)]
                    # 不自行选择追溯调整版本：多个同口径事实也进入复核。
                    if len(exact) == 1:
                        fact = exact[0]
                        evidence_ids = list(fact["evidence_ids"])
                        value = Decimal(fact["value"])
                        expected_unit = fact["unit"]
                        if claim["formula"]:
                            prior = dict(claim, start=f"{int(claim['start'][:4])-1}{claim['start'][4:]}",
                                         end=f"{int(claim['end'][:4])-1}{claim['end'][4:]}")
                            baseline = [f for f in candidates if matches(f, prior)]
                            if len(baseline) != 1 or Decimal(baseline[0]["value"]) <= 0:
                                check["reason"] = "同比缺少唯一同口径正基期；零/负基期需复核"
                                checks.append(check)
                                reviews.append(review(ev["evidence_id"], sentence + "：" + check["reason"], [ev["evidence_id"]] + evidence_ids, context.run_id))
                                used.extend([ev["evidence_id"]] + evidence_ids)
                                continue
                            ids = [fact["fact_id"], baseline[0]["fact_id"]]
                            calc = call(context, "calculation", "calculate.yoy", ids,
                                        lambda: context.calculations.calculate("yoy", ids, [], {}))
                            validate("Calculation", calc)
                            if (calc["formula_code"] != "yoy" or calc["input_fact_ids"] != ids
                                    or calc["input_calculation_ids"] or calc["unit"] != "percent"):
                                raise ValueError("计算工具返回了不匹配的公式、输入或单位")
                            calculations.append(calc)
                            check["calculation_id"] = calc["calculation_id"]
                            evidence_ids.extend(baseline[0]["evidence_ids"])
                            value = Decimal(calc["value"]) if calc["status"] == "verified" and calc["value"] is not None else None
                            expected_unit = "percent"
                            if value is None:
                                check["reason"] = calc["reason"] or "工具重算结果待复核"
                        check["source_evidence_ids"] = sorted(set(evidence_ids))
                        if value is not None:
                            if abs(claim["value"] - value) <= claim["quantum"] / 2:
                                check.update(decision="correct", error_type="none", severity="low",
                                             reason="与唯一同期间、同口径已复核事实或工具结果一致（按声明精度比较）")
                            else:
                                error_type = "number"
                                if not claim["formula"] and any(Decimal(claim["number"]) * SCALE[label] == value
                                                               for label in ("元", "万元", "亿元") if label != claim["label"]):
                                    error_type = "unit"
                                edit_sentence = re.sub(r"同比(?:增长|上升|下降|减少|下滑)", "同比变动", sentence) if claim["formula"] else sentence
                                check.update(decision="incorrect", error_type=error_type, severity="high",
                                             reason="明确声明与固定资料包内唯一已复核事实/工具结果不符",
                                             suggested_edit=NUMBER_RE.sub(f"{format(value, 'f')}{'%' if expected_unit == 'percent' else '元'}", edit_sentence, count=1))
                    elif len(exact) > 1:
                        check["source_evidence_ids"] = sorted({eid for f in exact for eid in f["evidence_ids"]})
                        check["reason"] = "同口径事实存在多个版本，需确认追溯调整与披露版本"
                    elif candidates:
                        scope_same = [f for f in candidates if f["statement_scope"] == claim["scope"]]
                        check["error_type"] = "period" if scope_same else "scope"
                        check["source_evidence_ids"] = sorted({eid for f in candidates for eid in f["evidence_ids"]})
                        check["reason"] = "未找到同期间/同口径事实；不能用其他期间或母公司/合并数直接判错"
                    else:
                        check["reason"] = "未找到证据完整且已人工核验的对应财务事实"
                elif ev["confidence"] < 0.9:
                    check["reason"] = "草稿 OCR/解析置信度不足，先复核原句"
                checks.append(check)
                used.extend([ev["evidence_id"]] + check["source_evidence_ids"])
                if check["decision"] == "needs_review":
                    reviews.append(review(ev["evidence_id"], sentence + "：" + check["reason"], [ev["evidence_id"]] + check["source_evidence_ids"], context.run_id))
        if not checks:
            reason = "草稿未返回可定位原句，需由 A 检查解析结果"
            warnings.append(reason)
            reviews.append(review(draft_id, reason, [], context.run_id))
        if reviews:
            warnings.append("存在待复核声明；未把证据不足自动判成错误")
        data = {"checks": checks, "summary": {decision: sum(c["decision"] == decision for c in checks)
                                              for decision in ("correct", "incorrect", "needs_review")}}
        # 相同工具 ID 不重复导出。
        unique = {calc["calculation_id"]: calc for calc in calculations}
        return finish(data, used, list(unique.values()), reviews, warnings, "VerificationData")
