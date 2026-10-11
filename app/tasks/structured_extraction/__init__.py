"""structured_extraction：数据结构化提取（选题 1，成员 A 工程主责）。"""

from app.tasks.structured_extraction.dictionary import (
    ANNOUNCEMENT_TYPES,
    FIELD_SPECS,
    RECORD_FIELDS,
    FieldSpec,
)
from app.tasks.structured_extraction.extract import ExtractionEngine, Segment
from app.tasks.structured_extraction.task import StructuredExtractionTask, build

__all__ = [
    "ANNOUNCEMENT_TYPES",
    "FIELD_SPECS",
    "RECORD_FIELDS",
    "FieldSpec",
    "ExtractionEngine",
    "Segment",
    "StructuredExtractionTask",
    "build",
]
