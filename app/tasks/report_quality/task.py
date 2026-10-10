"""公开、可重放的规则评分；不是对投资价值或写作质量的最终判断。"""
from app.tasks.research_verification import ResearchVerificationTask
from app.tasks.research_verification.task import METRICS
from app.tasks.research_verification.support import finish, load_evidence, review, validate

RUBRIC = [
    {"dimension": "accuracy", "weight": 40, "criterion": "每条已确认数值错误扣10分；未核实声明扣5分，上限40"},
    {"dimension": "traceability", "weight": 25, "criterion": "每条缺独立来源的声明扣5分，上限25"},
    {"dimension": "boundaries", "weight": 20, "criterion": "未明确合并/母公司口径扣10分；未明确期间扣10分"},
    {"dimension": "risks", "weight": 15, "criterion": "未出现风险说明扣8分；未出现反证或失效条件扣7分"},
]


class ReportQualityTask:
    task_type = "report_quality"

    def run(self, context, task_input):
        validate("QualityInput", task_input)
        reports = task_input["report_document_ids"]
        if not set(reports) <= set(context.document_ids):
            raise ValueError("报告必须属于固定输入资料包")
        scores, reviews, evidence_ids, calculations, warnings = [], [], [], {}, []
        # 契约没有 company_code；从外部事实中取得唯一公司，否则不猜测公司。
        source_docs = [d for d in context.document_ids if d not in reports]
        from app.tasks.research_verification.support import call
        facts = call(context, "tool_call", "quality.extract_facts", source_docs,
                     lambda: context.evidence.extract_facts(source_docs, list(METRICS.values())))
        for fact in facts:
            validate("Fact", fact)
        companies = {f["company_code"] for f in facts}
        for doc_id in reports:
            evidence, notes = load_evidence(context, doc_id)
            warnings.extend(notes)
            ids = [e["evidence_id"] for e in evidence]
            text = "\n".join(e["text"] for e in evidence)
            deductions, budgets = [], {r["dimension"]: r["weight"] for r in RUBRIC}

            def deduct(dimension, points, reason, source_ids):
                points = min(points, budgets[dimension])
                if points:
                    deductions.append({"dimension": dimension, "points": points,
                                       "reason": reason, "evidence_ids": list(source_ids)})
                    budgets[dimension] -= points

            if len(companies) == 1 and evidence:
                # 隔离其他被评分报告，避免把研报当成财务原始来源。
                from dataclasses import replace
                isolated = replace(context, document_ids=tuple(source_docs + [doc_id]))
                result = ResearchVerificationTask().run(isolated, {"draft_document_id": doc_id, "company_code": next(iter(companies))})
                for check in result["data"]["checks"]:
                    anchor = [check["draft_evidence_id"]] + check["source_evidence_ids"]
                    if check["decision"] != "correct":
                        deduct("accuracy", 10 if check["decision"] == "incorrect" else 5,
                               check["sentence"] + "：" + check["reason"], anchor)
                    if not check["source_evidence_ids"]:
                        deduct("traceability", 5, check["sentence"] + "：未定位独立来源", anchor)
                reviews.extend(result["review_items"])
                evidence_ids.extend(result["evidence_ids"])
                calculations.update((c["calculation_id"], c) for c in result["calculations"])
                warnings.extend(result["warnings"])
            else:
                reason = "无法从原始事实确定唯一公司，或报告缺少定位原文；准确性与来源维度暂不给分"
                deduct("accuracy", 40, reason, ids)
                deduct("traceability", 25, reason, ids)
            if not any(word in text for word in ("合并", "母公司")):
                deduct("boundaries", 10, "已解析片段未明确报表口径，须检查完整报告", ids)
            if not any(word in text for word in ("全年", "半年", "季度", "年末")):
                deduct("boundaries", 10, "已解析片段未明确期间，须检查完整报告", ids)
            if "风险" not in text:
                deduct("risks", 8, "已解析片段未出现风险说明，须检查完整报告", ids)
            if not any(word in text for word in ("反证", "失效", "不成立")):
                deduct("risks", 7, "已解析片段未出现反证/失效条件，须检查完整报告", ids)
            reason = "规则评分是暂定结果；需人工检查解析覆盖、风险实质与论证质量"
            reviews.append(review(doc_id, reason, ids, context.run_id))
            scores.append({"document_id": doc_id, "score": 100 - sum(d["points"] for d in deductions), "deductions": deductions})
            evidence_ids.extend(ids)
        unique_reviews = list({r["review_id"]: r for r in reviews}.values())
        data = {"rubric": RUBRIC, "report_scores": scores, "review_items": unique_reviews}
        warnings.append("评分规则 c-quality-v1；关键词存在仅表示可见，不代表风险或反证论述充分；最终质量需人工复核")
        return finish(data, evidence_ids, list(calculations.values()), unique_reviews, warnings, "QualityData")
