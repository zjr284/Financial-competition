"""HTTP 接口测试：对齐 contracts/openapi.yaml 的端点、错误结构与响应头。

覆盖方式：
  * 大部分用 `ApiServer.call()` 绕过 socket 直接调用路由，保证测试快且稳定；
  * 另有一个用真实 HTTP 连接的用例，验证 multipart 解析、Content-Length、
    ETag 与 X-Request-Id 这些只在 socket 路径上才会暴露的问题。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from api.server import ApiServer
from app.common.contracts import validate_contract
from app.errors import AppError


def _call(server: ApiServer, method: str, path: str, *, body=None, query=None) -> tuple[int, object]:
    """与真实 HTTP 路径语义一致：AppError 转成 (状态码, 契约 Error 结构)。

    `ApiServer.call()` 是进程内直调，异常会直接抛出；HTTP 层的
    `Handler._send_error` 才会把它转成状态码与响应体。测试统一走这里，
    保证断言的就是评审通过 HTTP 会看到的行为。
    """
    try:
        return server.call(method, path, body=body, query=query or {})
    except AppError as error:
        return error.http_status, error.payload()


@pytest.fixture()
def server(app) -> ApiServer:
    instance = ApiServer(app, port=0)
    return instance


def _multipart(filename: str, payload: bytes, **fields: str) -> tuple[str, bytes]:
    boundary = "----FinAgentTestBoundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + payload
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


# ---------------------------------------------------------------- 基本端点


def test_health(server: ApiServer) -> None:
    status, payload = _call(server, "GET", "/api/v1/health")
    assert status == 200
    validate_contract("Health", payload, label="Health")
    assert payload["api_version"] == "1.0.0"
    assert server.app.health()["details"]["limits"]["max_pdf_pages"] >= 1


def test_upload_document(server: ApiServer, samples_dir: Path) -> None:
    body = _multipart(
        "announcement_pledge_demo.txt",
        (samples_dir / "announcement_pledge_demo.txt").read_bytes(),
        disclosure_date="2026-09-01",
    )
    status, payload = _call(server, "POST", "/api/v1/documents", body=body)
    assert status == 201
    validate_contract("Document", payload, label="Document")
    assert payload["parse_status"] == "pending"

    # 相同内容重复上传必须返回同一 document_id，并保持 201
    status2, payload2 = _call(server, "POST", "/api/v1/documents", body=body)
    assert status2 == 201
    assert payload2["document_id"] == payload["document_id"]

    status3, fetched = _call(server, "GET", f"/api/v1/documents/{payload['document_id']}")
    assert status3 == 200
    assert fetched["sha256"] == payload["sha256"]


def test_upload_without_file_returns_validation_error(server: ApiServer) -> None:
    boundary = "----OnlyText"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="disclosure_date"\r\n\r\n2026-09-01\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    status, payload = _call(server, "POST", "/api/v1/documents", body=(f"multipart/form-data; boundary={boundary}", body))
    assert status == 400
    validate_contract("Error", payload, label="Error")
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["run_id"] is None


def test_unsupported_type_maps_to_415(server: ApiServer) -> None:
    boundary = "----Bad"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.exe"\r\n\r\n'
        f"MZ\r\n--{boundary}--\r\n"
    ).encode()
    status, payload = _call(server, "POST", "/api/v1/documents", body=(f"multipart/form-data; boundary={boundary}", body))
    assert status == 415
    assert payload["code"] == "DOCUMENT_UNSUPPORTED"
    assert payload["retryable"] is False


def test_document_content_is_raw_bytes(server: ApiServer, samples_dir: Path) -> None:
    body = _multipart("announcement_pledge_demo.txt", (samples_dir / "announcement_pledge_demo.txt").read_bytes())
    _, document = _call(server, "POST", "/api/v1/documents", body=body)
    status, payload = _call(server, "GET", f"/api/v1/documents/{document['document_id']}/content")
    assert status == 200
    assert payload["__raw__"] == (samples_dir / "announcement_pledge_demo.txt").read_bytes()
    assert payload["media_type"] == "text/plain"
    assert payload["sha256"] == document["sha256"]


def test_parse_and_evidence_endpoints(server: ApiServer, samples_dir: Path) -> None:
    body = _multipart("announcement_pledge_demo_cjk.pdf", (samples_dir / "announcement_pledge_demo_cjk.pdf").read_bytes())
    _, document = _call(server, "POST", "/api/v1/documents", body=body)

    status, manifest = _call(server, "POST", f"/api/v1/documents/{document['document_id']}/parse")
    assert status == 200
    assert manifest["status"] == "parsed"
    assert manifest["evidence_count"] > 0

    status, listing = _call(server, "GET", f"/api/v1/documents/{document['document_id']}/evidence")
    assert status == 200
    assert listing["evidence"]
    first = listing["evidence"][0]

    status, evidence = _call(server, "GET", f"/api/v1/evidence/{first['evidence_id']}")
    assert status == 200
    validate_contract("Evidence", evidence, label="Evidence")
    assert evidence["bbox"] == first["bbox"]


# ---------------------------------------------------------------- 错误映射


def test_unknown_document_returns_404(server: ApiServer) -> None:
    status, payload = _call(server, "GET", "/api/v1/documents/doc_missing")
    assert status == 404
    validate_contract("Error", payload, label="Error")
    assert payload["code"] == "RESOURCE_NOT_FOUND"


def test_unknown_evidence_returns_404(server: ApiServer) -> None:
    status, payload = _call(server, "GET", "/api/v1/evidence/ev_missing")
    assert status == 404
    assert payload["code"] == "RESOURCE_NOT_FOUND"


def test_unknown_path_returns_404(server: ApiServer) -> None:
    status, payload = _call(server, "GET", "/api/v1/nope")
    assert status == 404
    assert payload["code"] == "RESOURCE_NOT_FOUND"


def test_wrong_method_returns_allowed_list(server: ApiServer) -> None:
    status, payload = _call(server, "POST", "/api/v1/health", body={})
    assert status == 400
    assert payload["code"] == "VALIDATION_ERROR"
    assert "GET" in payload["details"]["allowed"]


def test_invalid_run_request_maps_to_400(server: ApiServer) -> None:
    status, payload = _call(
        server,
        "POST",
        "/api/v1/runs",
        body={"api_version": "1.0.0", "task_type": "nope", "document_ids": [], "task_input": {}},
    )
    assert status == 400
    validate_contract("Error", payload, label="Error")


# ---------------------------------------------------------------- Run 生命周期


def _upload(server: ApiServer, samples_dir: Path, filename: str) -> dict:
    body = _multipart(filename, (samples_dir / filename).read_bytes(), disclosure_date="2026-09-01")
    _, document = _call(server, "POST", "/api/v1/documents", body=body)
    return document


def _wait_terminal(server: ApiServer, run_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, run = _call(server, "GET", f"/api/v1/runs/{run_id}")
        if run["status"] in {"succeeded", "needs_review", "failed", "cancelled"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} 未在 {timeout}s 内结束")


def test_create_run_is_async_and_result_readable_after_finish(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_demo.txt")
    request = {
        "api_version": "1.0.0",
        "task_type": "structured_extraction",
        "document_ids": [document["document_id"]],
        "task_input": {"announcement_types": ["pledge"]},
    }
    status, run = _call(server, "POST", "/api/v1/runs", body=request)
    assert status == 202
    validate_contract("Run", run, label="Run")
    assert run["status"] in {"queued", "running"}

    finished = _wait_terminal(server, run["run_id"])
    assert finished["status"] == "succeeded"

    status, result = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/result")
    assert status == 200
    validate_contract("TaskResult", result, label="TaskResult")
    assert result["data"]["records"]


def test_result_conflicts_while_running(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_demo.txt")
    status, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        },
    )
    assert status == 202
    # 未终结前读取结果必须 409（契约：queued/running/failed 返回 409）
    _, current = _call(server, "GET", f"/api/v1/runs/{run['run_id']}")
    if current["status"] in {"queued", "running"}:
        status, payload = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/result")
        assert status == 409
        assert payload["code"] == "RUN_NOT_READY"
    _wait_terminal(server, run["run_id"])


def test_failed_run_returns_409_with_error_details(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_demo.txt")
    status, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "financial_analysis",
            "document_ids": [document["document_id"]],
            "task_input": {"company_code": "999999", "periods": ["2025-12-31"], "statement_scope": "consolidated"},
        },
    )
    assert status == 202
    finished = _wait_terminal(server, run["run_id"])
    assert finished["status"] == "failed"
    assert finished["error"]["code"] == "MISSING_INPUT"

    status, payload = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/result")
    assert status == 409
    assert payload["code"] == "RUN_NOT_READY"


def test_events_endpoint_paginates(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_demo.txt")
    _, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        },
    )
    _wait_terminal(server, run["run_id"])
    status, page = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/events", query={"limit": "2"})
    assert status == 200
    validate_contract("EventPage", page, label="EventPage")
    assert len(page["events"]) == 2
    assert page["next_cursor"] == "2"


def test_replay_endpoint(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_demo.txt")
    _, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        },
    )
    _wait_terminal(server, run["run_id"])
    status, report = _call(server, "POST", f"/api/v1/runs/{run['run_id']}/replay")
    assert status == 200
    assert report["input_hash_match"] and report["result_hash_match"] and report["trace_match"]
    assert report["chain"]["ok"] is True


def test_review_decision_endpoint(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_incomplete_demo.txt")
    _, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        },
    )
    finished = _wait_terminal(server, run["run_id"])
    assert finished["status"] == "needs_review"
    _, result = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/result")
    review_id = result["review_items"][0]["review_id"]

    status, item = server.call(
        "POST",
        f"/api/v1/reviews/{review_id}/decision",
        body={"decision": "accept", "reviewer": "member_b", "note": "已核对原文", "corrected_value": None},
    )
    assert status == 200
    validate_contract("ReviewItem", item, label="ReviewItem")
    assert item["status"] == "accepted"


def test_review_decision_invalid_payload_is_400(server: ApiServer, samples_dir: Path) -> None:
    document = _upload(server, samples_dir, "announcement_pledge_incomplete_demo.txt")
    _, run = server.call(
        "POST",
        "/api/v1/runs",
        body={
            "api_version": "1.0.0",
            "task_type": "structured_extraction",
            "document_ids": [document["document_id"]],
            "task_input": {"announcement_types": ["pledge"]},
        },
    )
    _wait_terminal(server, run["run_id"])
    _, result = _call(server, "GET", f"/api/v1/runs/{run['run_id']}/result")
    review_id = result["review_items"][0]["review_id"]
    status, payload = _call(
        server,
        "POST",
        f"/api/v1/reviews/{review_id}/decision",
        body={"decision": "maybe", "reviewer": "x", "note": "y", "corrected_value": None},
    )
    assert status == 400
    assert payload["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------- 真实 socket 路径


def test_real_http_roundtrip(server: ApiServer, samples_dir: Path) -> None:
    """走真实 TCP：验证 multipart、ETag、X-Request-Id 与 JSON 编码。"""
    server.start(background=True)
    try:
        base = f"http://127.0.0.1:{server.port}/api/v1"

        with urllib.request.urlopen(f"{base}/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
            assert response.headers["X-Request-Id"]
        assert health["api_version"] == "1.0.0"

        content_type, body = _multipart(
            "announcement_pledge_demo.txt",
            (samples_dir / "announcement_pledge_demo.txt").read_bytes(),
            disclosure_date="2026-09-01",
        )
        request = urllib.request.Request(
            f"{base}/documents", data=body, headers={"Content-Type": content_type}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 201
            assert response.headers["X-Request-Id"]
            document = json.loads(response.read().decode("utf-8"))

        with urllib.request.urlopen(f"{base}/documents/{document['document_id']}/content", timeout=10) as response:
            raw = response.read()
            assert response.headers["ETag"] == document["sha256"]
        assert raw == (samples_dir / "announcement_pledge_demo.txt").read_bytes()

        # 错误响应必须是契约 Error 结构而不是 HTML
        try:
            urllib.request.urlopen(f"{base}/documents/doc_missing", timeout=10)
            raise AssertionError("应当返回 404")
        except urllib.error.HTTPError as error:
            assert error.code == 404
            payload = json.loads(error.read().decode("utf-8"))
            validate_contract("Error", payload, label="Error")
    finally:
        server.stop()
