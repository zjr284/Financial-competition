"""运行配置。

接口约定要求“限制文件大小与页数的具体数值由部署配置设定并写入 README”，
因此所有限制都集中在此，并可通过环境变量覆盖：

  FINSCOPE_DATA_DIR      运行数据根目录（默认 <repo>/var）
  FINSCOPE_MAX_FILE_MB   单文件上限，默认 50
  FINSCOPE_MAX_PDF_PAGES 单 PDF 页数上限，默认 300
  FINSCOPE_OCR_ENABLED   是否允许 OCR 适配器，默认 0（离线环境无 OCR 引擎）
  FINSCOPE_RUN_STALE_SECONDS  running 悬挂多久判定为失联，默认 900 秒
  FINSCOPE_MODEL_ID      模型标识，默认 rule-based-offline
  FINSCOPE_MODEL_REVISION 模型修订号
  FINSCOPE_PROMPT_VERSION 提示词版本
  FINSCOPE_CODE_VERSION  代码版本
  FINSCOPE_EXTRACTION_BACKEND rules（默认）或 model（显式模型候选）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # 配置错误要显式失败，不静默回退
        raise ValueError(f"环境变量 {name} 必须是整数，当前为 {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 契约固定的上传白名单（docs/接口约定.md 第 1 节）
ALLOWED_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

ALLOWED_SUFFIXES = tuple(ALLOWED_MEDIA_TYPES)


@dataclass(frozen=True)
class Settings:
    """一次进程运行使用的不可变配置。"""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("FINSCOPE_DATA_DIR", REPO_ROOT / "var")))
    max_file_bytes: int = field(default_factory=lambda: _env_int("FINSCOPE_MAX_FILE_MB", 50) * 1024 * 1024)
    max_pdf_pages: int = field(default_factory=lambda: _env_int("FINSCOPE_MAX_PDF_PAGES", 300))
    ocr_enabled: bool = field(default_factory=lambda: _env_bool("FINSCOPE_OCR_ENABLED", False))
    # running 悬挂多久判定为失联（进程被杀/断电后没有机会写终态）
    run_stale_seconds: int = field(default_factory=lambda: _env_int("FINSCOPE_RUN_STALE_SECONDS", 900))
    model_id: str = field(default_factory=lambda: os.environ.get("FINSCOPE_MODEL_ID", "rule-based-offline"))
    model_revision: str = field(default_factory=lambda: os.environ.get("FINSCOPE_MODEL_REVISION", "v1"))
    prompt_version: str = field(default_factory=lambda: os.environ.get("FINSCOPE_PROMPT_VERSION", "prompt-v1"))
    code_version: str = field(default_factory=lambda: os.environ.get("FINSCOPE_CODE_VERSION", "member-a-0.1.0"))
    extraction_backend: str = field(default_factory=lambda: os.environ.get("FINSCOPE_EXTRACTION_BACKEND", "rules"))
    # 归一化坐标系：契约规定 0—1000
    norm_scale: int = 1000
    # 行合并容差（PDF 用户空间单位）
    line_y_tolerance: float = 3.0
    # OCR 置信度低于该值必须进入 needs_review
    ocr_confidence_floor: float = 0.60

    # ---- 派生路径 ----
    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "metadata.sqlite3"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_dirs(self) -> None:
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict[str, object]:
        """写入 README 与 /health 的部署限制说明。"""
        return {
            "data_dir": str(self.data_dir),
            "max_file_bytes": self.max_file_bytes,
            "max_file_mb": round(self.max_file_bytes / 1024 / 1024, 3),
            "max_pdf_pages": self.max_pdf_pages,
            "allowed_media_types": sorted(ALLOWED_MEDIA_TYPES.values()),
            "ocr_enabled": self.ocr_enabled,
            "ocr_confidence_floor": self.ocr_confidence_floor,
            "run_stale_seconds": self.run_stale_seconds,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "prompt_version": self.prompt_version,
            "code_version": self.code_version,
        }


def get_settings(**overrides: object) -> Settings:
    """取配置；测试可传覆盖值。"""
    base = Settings()
    if not overrides:
        return base
    return Settings(**{**{f: getattr(base, f) for f in base.__dataclass_fields__}, **overrides})  # type: ignore[arg-type]
