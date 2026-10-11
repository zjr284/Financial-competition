"""错误结构：严格对应 contracts.schema.json 的 $defs.Error。

HTTP 4xx 表示调用方输入问题，5xx 表示服务问题；运行途中发现的业务失败写入 Run.error，
不抛异常穿透到 HTTP 层。
"""

from __future__ import annotations

from typing import Any, Mapping


# 契约固定错误码（contracts.schema.json -> $defs.Error.code）
ERROR_CODES = frozenset(
    {
        "VALIDATION_ERROR",
        "DOCUMENT_UNSUPPORTED",
        "FILE_TOO_LARGE",
        "DOCUMENT_PARSE_FAILED",
        "RESOURCE_NOT_FOUND",
        "EVIDENCE_NOT_FOUND",
        "MISSING_INPUT",
        "PERIOD_MISMATCH",
        "SCOPE_MISMATCH",
        "CALCULATION_UNDEFINED",
        "MODEL_UNAVAILABLE",
        "RUN_NOT_READY",
        "REVIEW_REQUIRED",
        "INTERNAL_ERROR",
    }
)

# 错误码 -> 建议 HTTP 状态码
HTTP_STATUS = {
    "VALIDATION_ERROR": 400,
    "DOCUMENT_UNSUPPORTED": 415,
    "FILE_TOO_LARGE": 413,
    "DOCUMENT_PARSE_FAILED": 422,
    "RESOURCE_NOT_FOUND": 404,
    "EVIDENCE_NOT_FOUND": 404,
    "MISSING_INPUT": 400,
    "PERIOD_MISMATCH": 400,
    "SCOPE_MISMATCH": 400,
    "CALCULATION_UNDEFINED": 400,
    "MODEL_UNAVAILABLE": 503,
    "RUN_NOT_READY": 409,
    "REVIEW_REQUIRED": 409,
    "INTERNAL_ERROR": 500,
}

# 默认重试语义
RETRYABLE = frozenset({"MODEL_UNAVAILABLE", "INTERNAL_ERROR", "DOCUMENT_PARSE_FAILED"})


class AppError(Exception):
    """带契约错误码的应用异常。

    payload() 可直接作为 HTTP 响应体或 Run.error。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
        run_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"未知错误码: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = RETRYABLE.__contains__(code) if retryable is None else retryable
        self.run_id = run_id
        self.http_status = http_status or HTTP_STATUS.get(code, 500)

    def payload(self, run_id: str | None = None) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
            "run_id": self.run_id if run_id is None else run_id,
        }

    def with_run(self, run_id: str) -> "AppError":
        return AppError(
            self.code,
            self.message,
            details=self.details,
            retryable=self.retryable,
            run_id=run_id,
            http_status=self.http_status,
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"AppError({self.code!r}, {self.message!r}, details={self.details!r})"


def validation_error(message: str, **details: Any) -> AppError:
    return AppError("VALIDATION_ERROR", message, details=details)


def not_found(message: str, **details: Any) -> AppError:
    return AppError("RESOURCE_NOT_FOUND", message, details=details)


def unsupported(message: str, **details: Any) -> AppError:
    return AppError("DOCUMENT_UNSUPPORTED", message, details=details)
