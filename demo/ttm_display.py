"""C 的虚构 TTM 展示包，原始输入未复核时保留空区间。"""
import copy
import hashlib
import json

from app.tasks.valuation.samples.ttm_pe.demo import HERE, run_demo
from app.tasks.research_verification.support import validate


def fixture_paths():
    return {"ttm_" + name: HERE / name for name in ("annual.txt", "current.txt", "prior.txt", "market.txt")}


def package():
    inputs = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))
    for kind, key in (("Fact", "facts"), ("Evidence", "evidence"), ("Document", "documents")):
        for entity in inputs[key]:
            validate(kind, entity)
    display = copy.deepcopy(inputs)
    for document in display["documents"]:
        path = HERE / document["filename"]
        if path not in fixture_paths().values() or hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
            raise ValueError("TTM 样例原件不在白名单或哈希不一致")
        document["filename"] = "ttm_" + document["filename"]
    pending = copy.deepcopy(inputs)
    source = next(fact for fact in pending["facts"] if fact["fact_id"] == "fact_demo_999999_current")
    source.update(review_status="needs_review", reason="Synthetic pending review example")
    results = [run_demo(inputs), run_demo(pending)]
    for result in results:
        validate("TaskResult", result)
    display["synthetic"] = True
    display["pending_facts"] = pending["facts"]
    return display, results
