"""pytest 夹具：每个测试用独立的临时数据目录，互不污染。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT / "vendor" / "python", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import app.bootstrap as bootstrap  # noqa: E402

bootstrap.bootstrap()

from app.config import Settings  # noqa: E402
from app.container import App, build_app  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "var", ocr_enabled=False)


@pytest.fixture()
def app(settings: Settings) -> App:
    instance = build_app(settings)
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture()
def samples_dir() -> Path:
    return ROOT / "samples"


@pytest.fixture()
def contracts_dir() -> Path:
    return ROOT / "contracts"
