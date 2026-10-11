"""OCR 适配器。

本项目当前部署环境没有安装 OCR 引擎（Tesseract 等），因此这里的策略是：
  * 显式报告不可用，而不是静默返回空文本后当作“解析成功”；
  * 数字不清时由 parse 层写成 needs_review，交人工复核；
  * 一旦部署环境提供引擎，实现 OcrAdapter 即可接入，接口不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings


@dataclass(frozen=True)
class OcrAvailability:
    available: bool
    engine: str | None
    reason: str


def detect_engine() -> OcrAvailability:
    import shutil

    for candidate in ("tesseract", "tesseract.exe"):
        path = shutil.which(candidate)
        if path:
            return OcrAvailability(True, path, "找到 tesseract 可执行文件")
    try:  # pragma: no cover - 可选依赖
        import pytesseract  # type: ignore  # noqa: F401

        return OcrAvailability(True, "pytesseract", "找到 pytesseract 模块")
    except Exception:
        pass
    return OcrAvailability(False, None, "未检测到 OCR 引擎；扫描件必须人工录入或补齐依赖")


class OcrAdapter:
    """占位实现：不可用时抛结构化错误，可读页由调用方决定如何复核。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.availability = detect_engine()

    @property
    def available(self) -> bool:
        return bool(self.availability.available and self.settings.ocr_enabled)

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.ocr_enabled,
            "engine": self.availability.engine,
            "available": self.available,
            "reason": self.availability.reason,
            "confidence_floor": self.settings.ocr_confidence_floor,
        }

    def recognize(self, image_bytes: bytes, *, page: int) -> list[dict[str, Any]]:  # pragma: no cover - 无引擎环境不执行
        if not self.available:
            raise RuntimeError(
                "OCR 不可用："
                f"{self.availability.reason}（FINSCOPE_OCR_ENABLED={self.settings.ocr_enabled}）"
            )
        raise NotImplementedError("接入具体 OCR 引擎后实现：返回带 bbox 与 confidence 的文本块")
