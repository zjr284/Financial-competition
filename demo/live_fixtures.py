"""把合成文件送入实际 A 解析器；仅显式合成演示使用，不代签真人事实。"""
import json

from app.tasks.research_verification.support import stable_id
from demo.build_fixtures import ROOT, build
from demo.pdf_fixture import make_pdf


def seed(app):
    path = app.settings.data_dir / "reviewed_facts.json"
    if path.exists():
        raise ValueError("合成演示需要新的独立数据目录，不能覆盖现有事实清单")
    package = build()
    documents = {}
    evidence = {}
    for item in package["documents"]:
        lines = (ROOT / item["filename"]).read_text(encoding="utf-8").splitlines()
        raw = make_pdf(lines)
        document = app.upload_document(item["filename"].replace(".txt", ".pdf"), raw)
        documents[item["document_id"]] = document
        manifest = app.parse_document(document["document_id"])
        parsed = [app.get_evidence(eid) for eid in manifest["evidence_ids"]]
        for old in package["evidence"]:
            if old["document_id"] == item["document_id"]:
                matches = [ev for ev in parsed if ev["text"].strip() == old["text"].strip()]
                if len(matches) != 1:
                    raise ValueError("合成原句未在实际解析器中唯一定位")
                evidence[old["evidence_id"]] = matches[0]["evidence_id"]
    source = documents["doc_c_source"]
    facts = [{**fact, "fact_id": stable_id("fact", [source["document_id"], fact["fact_id"]]),
              "evidence_ids": [evidence[eid] for eid in fact["evidence_ids"]]} for fact in package["facts"]]
    manifest = {"version": 1, "documents": [{"document_id": source["document_id"],
                "sha256": source["sha256"], "facts": facts}]}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"synthetic": True, "documents": {key.removeprefix("doc_c_"): value["document_id"]
            for key, value in documents.items()}, "fact_manifest": str(path)}
