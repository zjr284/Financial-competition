"""事件日志与运行清单。

契约要求（docs/接口约定.md 第 5 节）：
  * 工具调用不得绕过 append_event；重试沿用同一个 run_id 并记录尝试次数
  * 日志保留文件 ID、哈希、调用摘要、版本和结果哈希
  * 不把原始敏感材料、模型响应或完整文档文本写入公开日志（这里统一 redact）
额外实现哈希链：每条事件的 chain_hash = H(prev_hash + 事件内容)，离线重放可校验轨迹未被改写。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.common.contracts import validate_contract
from app.common.ids import digest_of, now_iso, truncate
from app.config import Settings
from app.storage.db import Store


REDACT_LIMIT = 240
_SHA256 = re.compile(r"\b[a-f0-9]{64}\b")


def redact(message: str, *, limit: int = REDACT_LIMIT) -> str:
    """日志只留短摘要：单条消息截断到 limit，避免把全文写进公开日志。"""
    return truncate(message.replace("\n", " ").replace("\r", " "), limit)


class EventLog:
    """实现 contracts/ports.py 的 EventPort。"""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------ 追加
    def append(
        self,
        run_id: str,
        *,
        event_type: str,
        message: str,
        tool_name: str | None = None,
        input_hash: str | None = None,
        output_hash: str | None = None,
        attempt: int = 1,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.common.ids import make_id

        timestamp = now_iso()
        safe_message = redact(message)
        body = {
            "run_id": run_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "tool_name": tool_name,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "message": safe_message,
            "attempt": attempt,
        }
        event_id = make_id("event", body, self._existing_count(run_id), length=20)
        previous = self.last_event_hash(run_id)
        event = {
            "event_id": event_id,
            "run_id": run_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "tool_name": tool_name,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "message": safe_message,
        }
        validate_contract("Event", event, label="Event")
        chain_hash = digest_of({"prev": previous, **body, "event_id": event_id})

        with self.store.write() as conn:
            conn.execute(
                """INSERT INTO events (event_id, run_id, timestamp, event_type, tool_name, input_hash,
                        output_hash, message, payload, prev_hash, chain_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    run_id,
                    timestamp,
                    event_type,
                    tool_name,
                    input_hash,
                    output_hash,
                    safe_message,
                    json.dumps(payload or {}, ensure_ascii=False),
                    previous,
                    chain_hash,
                ),
            )
        self._write_jsonl(run_id, {"seq": self._existing_count(run_id), **event, "prev_hash": previous, "chain_hash": chain_hash})
        return event

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        """contracts/ports.py EventPort 的直接实现。"""
        self.append(
            run_id,
            event_type=event.get("event_type", "tool_call"),
            message=event.get("message", ""),
            tool_name=event.get("tool_name"),
            input_hash=event.get("input_hash"),
            output_hash=event.get("output_hash"),
            attempt=int(event.get("attempt", 1)),
            payload=event.get("payload"),
        )

    # ------------------------------------------------------------ 查询
    def _existing_count(self, run_id: str) -> int:
        with self.store.write() as conn:
            cur = conn.execute("SELECT COUNT(*) AS n FROM events WHERE run_id = ?", (run_id,))
            return int(cur.fetchone()["n"])

    def last_event_hash(self, run_id: str) -> str | None:
        with self.store.write() as conn:
            cur = conn.execute(
                "SELECT chain_hash FROM events WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)
            )
            row = cur.fetchone()
        return row["chain_hash"] if row else None

    def page(self, run_id: str, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        """EventPage 分页；cursor 为上一页最后一条的 seq。"""
        offset = 0
        if cursor:
            try:
                offset = int(cursor)
            except ValueError:
                offset = 0
        with self.store.write() as conn:
            cur = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY seq LIMIT ? OFFSET ?",
                (run_id, limit, offset),
            )
            rows = [dict(row) for row in cur.fetchall()]
        events = [
            {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "tool_name": row["tool_name"],
                "input_hash": row["input_hash"],
                "output_hash": row["output_hash"],
                "message": row["message"],
            }
            for row in rows
        ]
        next_cursor = None
        if len(rows) == limit:
            next_cursor = str(offset + limit)
        return {"events": events, "next_cursor": next_cursor}

    def all_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.store.write() as conn:
            cur = conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,))
            return [dict(row) for row in cur.fetchall()]

    def verify_chain(self, run_id: str) -> dict[str, Any]:
        """离线校验事件哈希链，返回 {ok, checked, broken_at}。"""
        events = self.all_events(run_id)
        previous: str | None = None
        for row in events:
            body = {
                "run_id": row["run_id"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "tool_name": row["tool_name"],
                "input_hash": row["input_hash"],
                "output_hash": row["output_hash"],
                "message": row["message"],
                "attempt": int(json.loads(row["payload"] or "{}").get("attempt", 1)),
            }
            expected = digest_of({"prev": previous, **body, "event_id": row["event_id"]})
            if expected != row["chain_hash"]:
                return {"ok": False, "checked": len(events), "broken_at": row["seq"]}
            previous = row["chain_hash"]
        return {"ok": True, "checked": len(events), "broken_at": None}

    def trace(self, run_id: str) -> list[tuple[str, str | None, str | None, str | None]]:
        """(event_type, tool_name, input_hash, output_hash) 序列，用于重放比对。"""
        return [
            (row["event_type"], row["tool_name"], row["input_hash"], row["output_hash"])
            for row in self.all_events(run_id)
        ]

    # ------------------------------------------------------------ 运行清单
    def _run_dir(self, run_id: str) -> Path:
        path = self.settings.runs_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_jsonl(self, run_id: str, record: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "events.jsonl"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_manifest(self, run_id: str, payload: dict[str, Any]) -> Path:
        path = self._run_dir(run_id) / "manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_result(self, run_id: str, result: dict[str, Any], *, needs_review: bool) -> Path:
        directory = self._run_dir(run_id)
        name = "result.needs_review.json" if needs_review else "result.json"
        path = directory / name
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_error(self, run_id: str, error: dict[str, Any]) -> Path:
        path = self._run_dir(run_id) / "error.json"
        path.write_text(json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_trace(self, run_id: str) -> Path:
        path = self._run_dir(run_id) / "trace.json"
        payload = {
            "run_id": run_id,
            "events": self.trace(run_id),
            "chain": self.verify_chain(run_id),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def run_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id)
