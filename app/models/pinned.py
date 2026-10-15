"""Run-local validated model responses, pinned by the event hash for offline replay."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from app.common.ids import digest_of
from app.errors import AppError
from app.models.remote import LIMIT, strict_json


class PinnedRunModel:
    def __init__(self, model, run_id, settings, events, versions, expected, *, replay=False):
        self.model, self.run_id, self.events = model, run_id, events
        self.directory = settings.runs_dir / run_id / "model-calls"
        self.versions, self.expected, self.replay = versions, expected, replay

    def infer_json(self, task_code, evidence_ids, schema_name, context):
        basis = {"task_code": task_code, "evidence_ids": list(evidence_ids),
                 "schema_name": schema_name, "context": context, "versions": self.versions}
        fingerprint = digest_of(basis)
        path = self.directory / (fingerprint + ".json")
        try:
            if path.exists():
                if path.stat().st_size > LIMIT:
                    raise ValueError("Oversize model snapshot")
                snapshot = strict_json(path.read_bytes())
                if self.expected.get(fingerprint) != digest_of(snapshot):
                    raise ValueError("Snapshot does not match original model event")
                if snapshot["input_hash"] != fingerprint or snapshot["versions"] != self.versions:
                    raise ValueError("Snapshot request/version mismatch")
                output = snapshot["output"]
                if snapshot["output_hash"] != digest_of(output):
                    raise ValueError("Snapshot output mismatch")
            elif self.replay:
                raise ValueError("Replay requires a pinned response; network fallback is forbidden")
            else:
                output = self.model.infer_json(task_code, evidence_ids, schema_name, context)
                snapshot = {"version": 1, "input_hash": fingerprint, "versions": self.versions,
                            "output": output, "output_hash": digest_of(output)}
                calls = getattr(self.model, "calls", [])
                if calls and hasattr(calls[-1], "to_dict"):
                    snapshot["call_record"] = calls[-1].to_dict()
                encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
                if len(encoded.encode()) > LIMIT:
                    raise ValueError("Oversize model snapshot")
                self.directory.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(encoded)
            # Replay validates this tool event against the original trace, not a mutable ledger.
            saved_hash = digest_of(snapshot)
            self.events.append(self.run_id, event_type="tool_call", tool_name="model.infer_json",
                               message="使用固定版本模型候选", input_hash=fingerprint, output_hash=saved_hash)
            self.events.append(self.run_id, event_type="model_call", tool_name=task_code,
                               message="模型候选已固定，数值仍须工具复核", input_hash=fingerprint,
                               output_hash=snapshot["output_hash"], payload=self.versions)
            self.expected[fingerprint] = saved_hash
            return deepcopy(output)
        except AppError:
            raise
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise AppError("MODEL_UNAVAILABLE", "模型响应快照缺失或未通过完整性校验") from exc
