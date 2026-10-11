"""models：模型适配器与 OCR 适配器。"""

from app.models.adapter import (
    ModelAdapter,
    ModelCallRecord,
    ModelResult,
    OfflineRuleModel,
    RemoteJsonModel,
    model_input_hash,
    validate_model_output,
)
from app.models.ocr import OcrAdapter, detect_engine

__all__ = [
    "ModelAdapter",
    "ModelCallRecord",
    "ModelResult",
    "OfflineRuleModel",
    "RemoteJsonModel",
    "model_input_hash",
    "validate_model_output",
    "OcrAdapter",
    "detect_engine",
]
