"""Keep both tests/ and eval/ independent of a developer's real cloud credentials."""
import pytest


@pytest.fixture(autouse=True)
def isolate_model_configuration(monkeypatch):
    for key in ("FINSCOPE_MODEL_ENDPOINT", "FINSCOPE_MODEL_KEY", "FINSCOPE_MODEL_ID",
                "FINSCOPE_MODEL_REVISION", "FINSCOPE_MODEL_TIMEOUT_SECONDS", "FINSCOPE_MODEL_MAX_ATTEMPTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FINSCOPE_MODEL_BACKEND", "offline-rule")
