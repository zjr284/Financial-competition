"""HTTP 服务：实现 contracts/openapi.yaml 的 10 个端点。

使用标准库 http.server，避免把评审环境绑定到额外 Web 框架；所有响应体都经过
JSON Schema 校验（契约是唯一事实来源），错误统一为 $defs.Error 结构，并带 X-Request-Id。
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from app import API_VERSION
from app.common.contracts import validate_contract
from app.config import Settings, get_settings
from app.container import App, get_app
from app.errors import AppError, not_found, validation_error
from api.multipart import MultipartError, parse_multipart


MAX_BODY = 512 * 1024 * 1024  # 硬上限，具体文件限制由 Settings.max_file_bytes 判定


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[..., tuple[int, Any]]]] = []

    def add(self, method: str, pattern: str, handler: Callable[..., tuple[int, Any]]) -> None:
        self.routes.append((method.upper(), pattern, handler))

    def match(self, method: str, path: str) -> tuple[Callable[..., tuple[int, Any]], dict[str, str]] | None:
        parts = [segment for segment in path.strip("/").split("/") if segment != ""]
        for route_method, pattern, handler in self.routes:
            if route_method != method.upper():
                continue
            pattern_parts = [segment for segment in pattern.strip("/").split("/") if segment != ""]
            if len(pattern_parts) != len(parts):
                continue
            params: dict[str, str] = {}
            for expected, actual in zip(pattern_parts, parts):
                if expected.startswith("{") and expected.endswith("}"):
                    params[expected[1:-1]] = actual
                elif expected != actual:
                    break
            else:
                return handler, params
        return None


class ApiService:
    """路由处理器集合；便于测试直接调用而不经过 socket。"""

    def __init__(self, app: App) -> None:
        self.app = app
        self.router = Router()
        self._register()

    # ------------------------------------------------------------ 路由
    def _register(self) -> None:
        r = self.router
        r.add("GET", "/api/v1/health", self.health)
        r.add("POST", "/api/v1/documents", self.upload_document)
        r.add("GET", "/api/v1/documents/{document_id}", self.get_document)
        r.add("GET", "/api/v1/documents/{document_id}/content", self.get_document_content)
        r.add("POST", "/api/v1/documents/{document_id}/parse", self.parse_document)
        r.add("GET", "/api/v1/documents/{document_id}/evidence", self.list_evidence)
        r.add("POST", "/api/v1/runs", self.create_run)
        r.add("GET", "/api/v1/runs/{run_id}", self.get_run)
        r.add("GET", "/api/v1/runs/{run_id}/result", self.get_result)
        r.add("GET", "/api/v1/runs/{run_id}/events", self.get_events)
        r.add("POST", "/api/v1/runs/{run_id}/replay", self.replay_run)
        r.add("GET", "/api/v1/evidence/{evidence_id}", self.get_evidence)
        r.add("POST", "/api/v1/reviews/{review_id}/decision", self.decide_review)

    # ------------------------------------------------------------ 端点
    def health(self, body: Any = None, query: dict[str, Any] | None = None) -> tuple[int, Any]:
        health = self.app.health()
        return 200, health

    def upload_document(self, body: Any = None, query: dict[str, Any] | None = None) -> tuple[int, Any]:
        content_type, form = body
        parsed = parse_multipart(form, content_type)
        if "file" not in parsed.files:
            raise validation_error("缺少 file 部分", expected="multipart/form-data with file")
        filename, _media_type, payload = parsed.files["file"]
        document = self.app.upload_document(
            filename,
            payload,
            source_url=parsed.get("source_url"),
            disclosure_date=parsed.get("disclosure_date"),
        )
        return 201, document

    def get_document(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        return 200, self.app.get_document(params["document_id"])

    def get_document_content(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        payload, document = self.app.get_document_content(params["document_id"])
        return 200, {"__raw__": payload, "media_type": document["media_type"], "sha256": document["sha256"]}

    def parse_document(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        force = bool((query or {}).get("force"))
        return 200, self.app.parse_document(params["document_id"], force=force)

    def list_evidence(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        page = (query or {}).get("page")
        evidence = self.app.parse.list_evidence(params["document_id"], page=int(page) if page else None)
        return 200, {"document_id": params["document_id"], "evidence": evidence}

    def create_run(self, body: Any = None, query: dict[str, Any] | None = None) -> tuple[int, Any]:
        request = body if isinstance(body, dict) else {}
        run = self.app.create_run(request)
        # 异步执行，接口立即返回 202 Run（与 OpenAPI 一致）
        worker = threading.Thread(target=self._safe_execute, args=(run["run_id"],), daemon=True)
        worker.start()
        return 202, run

    def get_run(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        return 200, self.app.get_run(params["run_id"])

    def get_result(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        return 200, self.app.get_result(params["run_id"])

    def get_events(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        cursor = (query or {}).get("cursor")
        limit = int((query or {}).get("limit", 200))
        return 200, self.app.get_events(params["run_id"], cursor=cursor, limit=limit)

    def replay_run(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        return 200, self.app.orchestrator.replay(params["run_id"])

    def get_evidence(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        return 200, self.app.get_evidence(params["evidence_id"])

    def decide_review(self, body: Any = None, query: dict[str, Any] | None = None, **params: str) -> tuple[int, Any]:
        decision = body if isinstance(body, dict) else {}
        return 200, self.app.orchestrator.decide_review(params["review_id"], decision)

    # ------------------------------------------------------------ 工具
    def _safe_execute(self, run_id: str) -> None:
        try:
            self.app.execute_run(run_id)
        except AppError:
            pass  # 失败状态已写入 Run.error
        except Exception:  # pragma: no cover - 兜底，避免线程静默死亡
            import sys

            print(f"[api] run {run_id} 执行异常:\n{traceback.format_exc()}", file=sys.stderr)

    def dispatch(
        self, method: str, path: str, body: Any, query: dict[str, Any]
    ) -> tuple[int, Any]:
        matched = self.router.match(method, path)
        if matched is None:
            allowed = sorted({route[0] for route in self.router.routes if _same_shape(route[1], path)})
            if allowed:
                raise AppError("VALIDATION_ERROR", f"路径不支持 {method}", details={"allowed": allowed, "path": path})
            raise not_found("路径不存在", path=path)
        handler, params = matched
        return handler(body, query, **params)


def _segments(path: str) -> list[str]:
    return [segment for segment in path.strip("/").split("/") if segment != ""]


def _same_shape(pattern: str, path: str) -> bool:
    pattern_parts = _segments(pattern)
    path_parts = _segments(path)
    if len(pattern_parts) != len(path_parts):
        return False
    for expected, actual in zip(pattern_parts, path_parts):
        if expected.startswith("{") and expected.endswith("}"):
            continue
        if expected != actual:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = f"FinResearchAgent/{API_VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 入口
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------ 处理
    def _handle(self, method: str) -> None:
        request_id = uuid.uuid4().hex[:16]
        parsed = urlparse(self.path)
        query = {key: value[0] for key, value in parse_qs(parsed.query).items()}
        service: ApiService = self.server.service  # type: ignore[attr-defined]

        try:
            body = self._read_body() if method == "POST" else None
            status, payload = service.dispatch(method, parsed.path, body, query)
        except AppError as error:
            self._send_error(error, request_id)
            return
        except MultipartError as error:
            self._send_error(validation_error(f"multipart 解析失败：{error}"), request_id)
            return
        except Exception as error:  # pragma: no cover - 兜底
            self._send_error(
                AppError("INTERNAL_ERROR", f"服务内部错误：{type(error).__name__}", details={"hint": str(error)}),
                request_id,
            )
            return

        if isinstance(payload, dict) and "__raw__" in payload:
            self._send_raw(payload, request_id)
            return
        self._send_json(status, payload, request_id)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        if length > MAX_BODY:
            raise AppError("FILE_TOO_LARGE", "请求体超过服务硬上限", details={"max_body_bytes": MAX_BODY})
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if content_type.lower().startswith("multipart/form-data"):
            return content_type, raw
        if "application/json" in content_type.lower() or raw.strip().startswith(b"{"):
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise validation_error(f"JSON 解析失败：{error}") from error
        return raw

    # ------------------------------------------------------------ 响应
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _send_json(self, status: int, payload: Any, request_id: str) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Request-Id", request_id)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_raw(self, payload: dict[str, Any], request_id: str) -> None:
        data: bytes = payload["__raw__"]
        self.send_response(200)
        self.send_header("Content-Type", payload.get("media_type", "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", payload.get("sha256", ""))
        self.send_header("X-Request-Id", request_id)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, error: AppError, request_id: str) -> None:
        payload = error.payload()
        validate_contract("Error", payload, label="Error")
        self._send_json(error.http_status, payload, request_id)

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover - 日志走 EventLog
        import sys

        print(f"[api] {self.address_string()} {fmt % args}", file=sys.stderr)


class ApiServer:
    """可编程启动的 HTTP 服务，端口 0 表示随机可用端口（测试用）。"""

    def __init__(self, app: App | None = None, *, host: str = "127.0.0.1", port: int = 8000, settings: Settings | None = None) -> None:
        self.app = app or get_app(settings)
        self.host = host
        self.port = port
        self.service = ApiService(self.app)
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, *, background: bool = False) -> "ApiServer":
        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.service = self.service  # type: ignore[attr-defined]
        httpd.daemon_threads = True
        self.httpd = httpd
        self.port = httpd.server_address[1]
        if background:
            self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def serve_forever(self) -> None:
        assert self.httpd is not None
        self.httpd.serve_forever()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------ 测试辅助
    def call(self, method: str, path: str, *, body: Any = None, query: dict[str, Any] | None = None) -> tuple[int, Any]:
        """绕过 socket 直接调用路由，供单元测试使用。"""
        return self.service.dispatch(method, path, body, query or {})

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"


def serve(host: str = "127.0.0.1", port: int = 8000, settings: Settings | None = None) -> None:
    server = ApiServer(port=port, host=host, settings=settings).start()
    print(f"金融投研智能体 API 已启动：{server.base_url}  （Ctrl+C 停止）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
