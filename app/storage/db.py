"""SQLite 元数据仓库与内容寻址快照库。

设计要点（对应成员 A 验收标准）：
  * 同一 PDF 重复登记得到稳定 document_id：ID 由 (sha256, source_url, disclosure_date) 派生。
  * 原始字节按 SHA-256 存于 blobs/<前两位>/<哈希>，文件名即内容指纹，只写一次。
  * 解析产物（行/表格/证据）落库，下游任务只读 evidence，不重复解析原文件。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.errors import not_found


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    document_id      TEXT PRIMARY KEY,
    doc_key          TEXT NOT NULL UNIQUE,
    filename         TEXT NOT NULL,
    media_type       TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    source_url       TEXT,
    disclosure_date  TEXT,
    ingested_at      TEXT NOT NULL,
    page_count       INTEGER NOT NULL,
    parse_status     TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    parse_manifest   TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id        TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL REFERENCES documents(document_id),
    page               INTEGER NOT NULL,
    bbox               TEXT NOT NULL,
    text               TEXT NOT NULL,
    extraction_method  TEXT NOT NULL,
    confidence         REAL NOT NULL,
    table_id           TEXT,
    row                INTEGER,
    column_index       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_evidence_document ON evidence(document_id, page);

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    task_type    TEXT NOT NULL,
    status       TEXT NOT NULL,
    document_ids TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    payload      TEXT NOT NULL,
    result       TEXT,
    task_input   TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    tool_name   TEXT,
    input_hash  TEXT,
    output_hash TEXT,
    message     TEXT NOT NULL,
    payload     TEXT,
    prev_hash   TEXT,
    chain_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);

CREATE TABLE IF NOT EXISTS reviews (
    review_id    TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    reviewer     TEXT,
    note         TEXT,
    corrected_value TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS idempotency (
    key         TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class Store:
    """线程安全的 SQLite 封装。HTTP 服务使用多线程，因此写操作加进程内锁。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    # ------------------------------------------------------------ 事务
    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ 文件快照
    def blob_path(self, sha256: str) -> Path:
        return self.settings.blobs_dir / sha256[:2] / sha256

    def put_blob(self, sha256: str, payload: bytes) -> Path:
        """内容寻址写入；已存在则复用，不覆盖（更正公告是新文件）。"""
        target = self.blob_path(sha256)
        if target.exists() and target.stat().st_size == len(payload):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return target

    def read_blob(self, sha256: str) -> bytes:
        path = self.blob_path(sha256)
        if not path.exists():
            raise not_found("原始文件快照缺失", sha256=sha256)
        return path.read_bytes()

    # ------------------------------------------------------------ documents
    def insert_document(self, row: dict[str, Any]) -> None:
        with self.write() as conn:
            conn.execute(
                """INSERT INTO documents (document_id, doc_key, filename, media_type, sha256,
                        source_url, disclosure_date, ingested_at, page_count, parse_status,
                        size_bytes, parse_manifest)
                   VALUES (:document_id, :doc_key, :filename, :media_type, :sha256, :source_url,
                        :disclosure_date, :ingested_at, :page_count, :parse_status, :size_bytes,
                        :parse_manifest)""",
                row,
            )

    def find_document_by_key(self, doc_key: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM documents WHERE doc_key = ?", (doc_key,))
            row = cur.fetchone()
        return dict(row) if row else None

    def get_document_row(self, document_id: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,))
            row = cur.fetchone()
        if row is None:
            raise not_found("文档不存在", document_id=document_id)
        return dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM documents ORDER BY ingested_at")
            return [dict(row) for row in cur.fetchall()]

    def update_document_parse(self, document_id: str, *, page_count: int, parse_status: str, manifest: dict[str, Any]) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE documents SET page_count = ?, parse_status = ?, parse_manifest = ? WHERE document_id = ?",
                (page_count, parse_status, json.dumps(manifest, ensure_ascii=False), document_id),
            )

    # ------------------------------------------------------------ evidence
    def insert_evidence(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        payload = [
            (
                row["evidence_id"],
                row["document_id"],
                row["page"],
                json.dumps(row["bbox"]),
                row["text"],
                row["extraction_method"],
                row["confidence"],
                row.get("table_id"),
                row.get("row"),
                row.get("column"),
            )
            for row in rows
        ]
        with self.write() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO evidence (evidence_id, document_id, page, bbox, text,
                        extraction_method, confidence, table_id, row, column_index)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                payload,
            )
        return len(payload)

    def get_evidence_row(self, evidence_id: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,))
            row = cur.fetchone()
        if row is None:
            raise not_found("证据不存在", evidence_id=evidence_id)
        return dict(row)

    def list_evidence(self, document_id: str, *, page: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM evidence WHERE document_id = ?"
        params: list[Any] = [document_id]
        if page is not None:
            sql += " AND page = ?"
            params.append(page)
        sql += " ORDER BY page, row, column_index, evidence_id"
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------ runs
    def insert_run(self, row: dict[str, Any]) -> None:
        with self.write() as conn:
            conn.execute(
                """INSERT INTO runs (run_id, task_type, status, document_ids, created_at, started_at,
                        finished_at, payload, result, task_input, error)
                   VALUES (:run_id, :task_type, :status, :document_ids, :created_at, :started_at,
                        :finished_at, :payload, :result, :task_input, :error)""",
                row,
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        payload: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        result: dict[str, Any] | None = None,
        touch_result: bool = False,
        error: dict[str, Any] | None = None,
        touch_error: bool = False,
    ) -> None:
        """payload 为已序列化 JSON 文本；touch_result / touch_error 为 True 时按传入值（含 None）覆盖该列。"""
        with self.write() as conn:
            conn.execute(
                """UPDATE runs SET
                        status = ?,
                        payload = ?,
                        started_at = COALESCE(?, started_at),
                        finished_at = COALESCE(?, finished_at),
                        result = CASE WHEN ? THEN ? ELSE result END,
                        error  = CASE WHEN ? THEN ? ELSE error  END
                   WHERE run_id = ?""",
                (
                    status,
                    payload,
                    started_at,
                    finished_at,
                    1 if touch_result else 0,
                    None if result is None else json.dumps(result, ensure_ascii=False),
                    1 if touch_error else 0,
                    None if error is None else json.dumps(error, ensure_ascii=False),
                    run_id,
                ),
            )

    def get_run_row(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise not_found("任务不存在", run_id=run_id)
        return dict(row)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM runs ORDER BY created_at")
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------ reviews
    def upsert_review(self, row: dict[str, Any]) -> None:
        with self.write() as conn:
            conn.execute(
                """INSERT INTO reviews (review_id, run_id, target_id, reason, status, evidence_ids,
                        reviewer, note, corrected_value, updated_at)
                   VALUES (:review_id, :run_id, :target_id, :reason, :status, :evidence_ids,
                        :reviewer, :note, :corrected_value, :updated_at)
                   ON CONFLICT(review_id) DO UPDATE SET
                        status = excluded.status,
                        reviewer = excluded.reviewer,
                        note = excluded.note,
                        corrected_value = excluded.corrected_value,
                        updated_at = excluded.updated_at""",
                row,
            )

    def get_review_row(self, review_id: str) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,))
            row = cur.fetchone()
        if row is None:
            raise not_found("复核项不存在", review_id=review_id)
        return dict(row)

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM reviews WHERE run_id = ? ORDER BY review_id", (run_id,))
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------ idempotency
    def remember_idempotency(self, key: str, run_id: str, created_at: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency (key, run_id, created_at) VALUES (?, ?, ?)",
                (key, run_id, created_at),
            )

    def recall_idempotency(self, key: str) -> str | None:
        with self._lock:
            cur = self._conn.execute("SELECT run_id FROM idempotency WHERE key = ?", (key,))
            row = cur.fetchone()
        return row["run_id"] if row else None
