"""本地 CPU OCR：RapidOCR（内置 ONNX 模型）与 PDFium 页面渲染。"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import math
import threading
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.config import Settings

_PDF_LOCK = threading.Lock()  # PDFium 不允许跨线程同时调用。
_MODEL_NAMES = ("PP-OCRv6_det_small.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "PP-OCRv6_rec_small.onnx")


@dataclass(frozen=True)
class OcrAvailability:
    available: bool
    engine: str | None
    reason: str


def detect_engine() -> OcrAvailability:
    for module in ("rapidocr", "onnxruntime", "pypdfium2", "PIL"):
        if importlib.util.find_spec(module) is None:
            return OcrAvailability(False, None, f"缺少本地 OCR 依赖 {module}；安装项目 ocr extra")
    spec = importlib.util.find_spec("rapidocr")
    root = Path(spec.origin).parent / "models"
    if not all((root / name).is_file() for name in _MODEL_NAMES):
        return OcrAvailability(False, None, "本地 OCR 模型不完整；须在准备阶段安装模型，不自动下载")
    return OcrAvailability(True, "rapidocr-onnxruntime", "本地 OCR 依赖已找到，模型在首次使用时加载")


class OcrAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.availability = detect_engine()
        self._engine: Any = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.settings.ocr_enabled and self.availability.available)

    def describe(self) -> dict[str, Any]:
        return {"enabled": self.settings.ocr_enabled, "engine": self.availability.engine,
                "available": self.available, "reason": self.availability.reason,
                "confidence_floor": self.settings.ocr_confidence_floor,
                "max_pages": self.settings.ocr_max_pages, "max_side": 2400}

    def provenance(self) -> str:
        """仅返回版本及模型摘要，不暴露安装路径或模型正文。"""
        models = self._model_files()
        digest = hashlib.sha256()
        for model in models:
            digest.update(model.name.encode())
            digest.update(hashlib.sha256(model.read_bytes()).digest())
        return (f"rapidocr/{version('rapidocr')} onnxruntime/{version('onnxruntime')} "
                f"pdfium/{version('pypdfium2')} models_sha256={digest.hexdigest()}")

    def recognize(self, image_bytes: bytes, *, page: int) -> list[dict[str, Any]]:
        if not self.available:
            raise RuntimeError("OCR 未启用或依赖不完整")
        if page < 1:
            raise ValueError("OCR 页码从 1 开始")
        from PIL import Image
        from rapidocr import RapidOCR
        with Image.open(io.BytesIO(image_bytes)) as image:
            width, height = image.size
            if width < 1 or height < 1 or width * height > 6_000_000:
                raise ValueError("OCR 图像尺寸超限")
        with self._lock:
            if self._engine is None:
                # 保留低分候选用于人工复核，不静默丢弃低分数字。
                import onnxruntime
                onnxruntime.disable_telemetry_events()
                det, cls, rec = self._model_files()
                self._engine = RapidOCR(params={
                    "Global.text_score": 0.0, "Global.log_level": "critical",
                    "Det.model_path": str(det), "Cls.model_path": str(cls), "Rec.model_path": str(rec),
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            output = self._engine(image_bytes)
            raw = zip(output.boxes, output.txts, output.scores) if output.txts else []
        rows = []
        for box, text, score, *_ in raw or []:
            score = float(score)
            if not isinstance(text, str) or not text.strip():
                continue
            points = [(float(p[0]), float(p[1])) for p in box]
            if len(points) != 4 or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("OCR 返回非法坐标或分数")
            if not all(math.isfinite(v) for p in points for v in p):
                raise ValueError("OCR 返回非有限坐标")
            bbox = [max(0, min(1000, math.floor(min(p[0] for p in points) / width * 1000))),
                    max(0, min(1000, math.floor(min(p[1] for p in points) / height * 1000))),
                    max(0, min(1000, math.ceil(max(p[0] for p in points) / width * 1000))),
                    max(0, min(1000, math.ceil(max(p[1] for p in points) / height * 1000)))]
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("OCR 返回空区域")
            rows.append({"page": page, "text": text.strip(), "bbox": bbox, "confidence": score})
        return sorted(rows, key=lambda row: (row["bbox"][1], row["bbox"][0]))

    @staticmethod
    def _model_files() -> list[Path]:
        import rapidocr
        root = Path(rapidocr.__file__).parent / "models"
        models = [root / name for name in _MODEL_NAMES]
        if not all(p.is_file() for p in models):
            raise RuntimeError("本地 OCR 模型缺失；须在联网准备阶段安装完整模型，不自动下载")
        return models

    def recognize_pdf(self, payload: bytes, pages: list[int]) -> tuple[dict[int, list[dict]], list[str]]:
        """渲染旋转后的 CropBox；逐页释放图像，限制页面数及像素数。"""
        if not self.available:
            return {}, ["OCR 未启用或本地依赖不完整"]
        import pypdfium2 as pdfium
        results: dict[int, list[dict]] = {}
        warnings: list[str] = []
        selected = pages[:self.settings.ocr_max_pages]
        if len(selected) < len(pages):
            warnings.append(f"OCR 达到每份文件 {self.settings.ocr_max_pages} 页上限，剩余无文本页未识别")
        with _PDF_LOCK, pdfium.PdfDocument(payload) as document:
            for number in selected:
                try:
                    pdf_page = document[number - 1]
                    try:
                        width, height = pdf_page.get_size()
                        scale = min(2.5, 2400 / max(width, height))
                        bitmap = pdf_page.render(scale=scale)
                        try:
                            image = bitmap.to_pil()
                            try:
                                output = io.BytesIO()
                                image.save(output, format="PNG")
                            finally:
                                image.close()
                        finally:
                            bitmap.close()
                    finally:
                        pdf_page.close()
                    results[number] = self.recognize(output.getvalue(), page=number)
                    if not results[number]:
                        warnings.append(f"OCR 第 {number} 页未识别到文字，仍需人工复核")
                except Exception as exc:
                    warnings.append(f"OCR 第 {number} 页失败（{type(exc).__name__}），仍需人工复核")
        return results, warnings
