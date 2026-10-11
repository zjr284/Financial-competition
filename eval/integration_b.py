"""只读上游 B 分支，在临时目录验证实际计算适配器，不合并/修改 B 模块。"""
import argparse
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import app
from app.tasks.research_verification import ResearchVerificationTask
from demo.adapters import FixturePorts, load_package


def run(reference):
    files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", reference,
                                     "--", "app/facts", "app/tools"], text=True).splitlines()
    if not files:
        raise ValueError("所选分支没有 B 的 facts/tools 模块")
    output = Path(__file__).resolve().parent / "output"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="c-b-integration-", dir=output) as directory:
        root = Path(directory).resolve()
        if root.parent != output.resolve():
            raise ValueError("临时目录必须处于本工作区 eval/output 内")
        for filename in files:
            target = root / filename
            if root not in target.resolve().parents:
                raise ValueError("上游路径超出临时目录")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", f"{reference}:{filename}"]))
        app.__path__.append(str(root / "app"))
        from app.tools import FinancialCalculator
        ports = FixturePorts(load_package())
        context = replace(ports.context(document_ids=["doc_c_source", "doc_c_success"]),
                          calculations=FinancialCalculator())
        result = ResearchVerificationTask().run(context, {"draft_document_id":"doc_c_success", "company_code":"999999"})
        assert result["data"]["summary"] == {"correct":2, "incorrect":0, "needs_review":0}
        assert result["calculations"][0]["value"] == "20"
        assert any(e["tool_name"] == "register_facts" for e in ports.events)
        print("B adapter integration passed: current/prior Fact registration, yoy=20%, source references")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default="upstream/feat/finance-valuation")
    run(parser.parse_args().ref)
