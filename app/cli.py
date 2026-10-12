#!/usr/bin/env python3
"""命令行入口：登记、解析、跑任务、重放、启动服务。

用法（在项目根目录运行）：
  python -m app.cli serve --port 8000
  python -m app.cli ingest samples/announcements_demo.txt --disclosure-date 2026-09-01
  python -m app.cli parse doc_xxx
  python -m app.cli run structured_extraction --documents doc_xxx --announcement-types pledge,bid_award,equity_change
  python -m app.cli replay run_xxx
  python -m app.cli events run_xxx
  python -m app.cli evidence ev_xxx
  python -m app.cli verify-contracts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import app.bootstrap as bootstrap

bootstrap.bootstrap()

from app.common.contracts import schema  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.container import build_app  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.tasks import IMPLEMENTED, TASK_TYPES  # noqa: E402


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_serve(args: argparse.Namespace) -> int:
    from api.server import serve

    serve(host=args.host, port=args.port)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        document = app.upload_path(
            args.path, source_url=args.source_url, disclosure_date=args.disclosure_date
        )
        if args.parse:
            document["parse"] = app.parse_document(document["document_id"])
        _print(document)
        return 0
    finally:
        app.close()


def cmd_parse(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        _print(app.parse_document(args.document_id, force=args.force))
        return 0
    finally:
        app.close()


def cmd_run(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        task_input = json.loads(args.task_input) if args.task_input else _default_input(args)
        request = {
            "api_version": "1.0.0",
            "task_type": args.task_type,
            "document_ids": args.documents,
            "task_input": task_input,
        }
        run = app.run_task(request, sync=True)
        _print(run)
        if run["status"] in {"succeeded", "needs_review"}:
            result = app.get_result(run["run_id"])
            print("\n--- TaskResult ---")
            _print(result)
            print(f"\n运行清单目录: {app.events.run_dir(run['run_id'])}")
        return 0 if run["status"] != "failed" else 1
    finally:
        app.close()


def _default_input(args: argparse.Namespace) -> dict[str, object]:
    if args.task_type == "structured_extraction":
        return {"announcement_types": args.announcement_types}
    raise SystemExit(f"任务 {args.task_type} 需要显式 --task-input JSON（该题由其他成员实现）")


def cmd_replay(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        report = app.orchestrator.replay(args.run_id)
        _print(report)
        ok = report["result_hash_match"] and report["input_hash_match"] and report["trace_match"]
        print(f"\n重放结论：{'一致' if ok else '不一致'}（result_hash_match={report['result_hash_match']}, "
              f"trace_match={report['trace_match']}）")
        return 0 if ok else 1
    finally:
        app.close()


def cmd_events(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        page = app.get_events(args.run_id, limit=args.limit)
        _print(page)
        _print(app.events.verify_chain(args.run_id))
        return 0
    finally:
        app.close()


def cmd_evidence(args: argparse.Namespace) -> int:
    app = build_app(get_settings())
    try:
        _print(app.get_evidence(args.evidence_id))
        return 0
    finally:
        app.close()


def cmd_export(args: argparse.Namespace) -> int:
    """导出结果：JSON / CSV / HTML。"""
    app = build_app(get_settings())
    try:
        outcome = app.export_run(args.run_id, formats=args.format, out_dir=args.out_dir)
        _print(outcome)
        return 0
    finally:
        app.close()


def cmd_recover(args: argparse.Namespace) -> int:
    """把长时间停在 running 的 Run 改判为 failed（进程被杀/断电后的兜底）。"""
    app = build_app(get_settings())
    try:
        recovered = app.recover_stuck_runs(stale_seconds=args.stale_seconds)
        _print({"count": len(recovered), "recovered": recovered})
        return 0
    finally:
        app.close()


def cmd_verify_contracts(args: argparse.Namespace) -> int:
    import subprocess

    script = Path(__file__).resolve().parent.parent / "contracts" / "validate_contracts.py"
    completed = subprocess.run([sys.executable, str(script)], check=False)
    return completed.returncode


def cmd_selftest(args: argparse.Namespace) -> int:
    """一键端到端：登记样例 -> 解析 -> 跑结构化提取 -> 重放，并打印契约校验结果。"""
    root = Path(__file__).resolve().parent.parent
    sample = root / "samples" / "announcement_pledge_demo.txt"
    if not sample.exists():
        print(f"缺少样例文件：{sample}", file=sys.stderr)
        return 2
    app = build_app(get_settings())
    try:
        document = app.upload_path(str(sample), disclosure_date="2026-09-01")
        manifest = app.parse_document(document["document_id"])
        run = app.run_task(
            {
                "api_version": "1.0.0",
                "task_type": "structured_extraction",
                "document_ids": [document["document_id"]],
                "task_input": {"announcement_types": ["pledge", "bid_award", "equity_change"]},
            }
        )
        result = app.get_result(run["run_id"])
        replay = app.orchestrator.replay(run["run_id"])
        summary = {
            "document": document,
            "parse": manifest,
            "run": run,
            "record_count": len(result["data"]["records"]),
            "review_items": len(result["review_items"]),
            "evidence_count": len(result["evidence_ids"]),
            "replay_ok": replay["result_hash_match"] and replay["trace_match"],
            "run_dir": str(app.events.run_dir(run["run_id"])),
        }
        _print(summary)
        return 0
    finally:
        app.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="金融投研智能体（成员 A 模块）命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 HTTP 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    ingest = sub.add_parser("ingest", help="登记文件")
    ingest.add_argument("path")
    ingest.add_argument("--source-url", default=None)
    ingest.add_argument("--disclosure-date", default=None)
    ingest.add_argument("--parse", action="store_true", help="登记后立即解析")
    ingest.set_defaults(func=cmd_ingest)

    parse = sub.add_parser("parse", help="解析已登记文档")
    parse.add_argument("document_id")
    parse.add_argument("--force", action="store_true")
    parse.set_defaults(func=cmd_parse)

    run = sub.add_parser("run", help="启动任务并同步等待结果")
    run.add_argument("task_type", choices=list(TASK_TYPES))
    run.add_argument("--documents", nargs="+", required=True)
    run.add_argument("--task-input", default=None, help="task_input 的 JSON 字符串")
    run.add_argument(
        "--announcement-types",
        nargs="+",
        default=["pledge", "bid_award", "equity_change"],
        help="structured_extraction 的 announcement_types",
    )
    run.set_defaults(func=cmd_run)

    replay = sub.add_parser("replay", help="离线重放并比对哈希")
    replay.add_argument("run_id")
    replay.set_defaults(func=cmd_replay)

    events = sub.add_parser("events", help="查看事件与哈希链校验")
    events.add_argument("run_id")
    events.add_argument("--limit", type=int, default=200)
    events.set_defaults(func=cmd_events)

    evidence = sub.add_parser("evidence", help="查看单条证据")
    evidence.add_argument("evidence_id")
    evidence.set_defaults(func=cmd_evidence)

    verify = sub.add_parser("verify-contracts", help="运行 contracts/validate_contracts.py")
    verify.set_defaults(func=cmd_verify_contracts)

    recover = sub.add_parser("recover", help="把长时间停在 running 的失联 Run 改判为 failed")
    recover.add_argument(
        "--stale-seconds",
        type=int,
        default=None,
        help="超过该秒数仍为 running 则判为失联；默认取 FINSCOPE_RUN_STALE_SECONDS（900）",
    )
    recover.set_defaults(func=cmd_recover)

    export = sub.add_parser("export", help="导出结果（JSON/CSV/HTML）")
    export.add_argument("run_id")
    export.add_argument(
        "--format",
        nargs="+",
        default=["json", "csv", "html"],
        choices=["json", "csv", "html"],
        help="导出格式，可多选",
    )
    export.add_argument("--out-dir", default=None, help="输出目录；默认为 <data_dir>/exports/<run_id>")
    export.set_defaults(func=cmd_export)

    selftest = sub.add_parser("selftest", help="端到端自检（登记->解析->任务->重放）")
    selftest.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AppError as error:
        _print(error.payload())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
