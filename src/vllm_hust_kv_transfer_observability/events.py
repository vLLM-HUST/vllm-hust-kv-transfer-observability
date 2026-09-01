# SPDX-License-Identifier: Apache-2.0
"""Process-safe JSONL event sink extracted from legacy B134 instrumentation."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class JsonlKVTransferEventSink:
    """Append bounded, address-free KV transfer events to an explicit path."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._file_descriptor: int | None = None
        self._open_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _descriptor(self) -> int:
        if self.path is None:
            raise RuntimeError("event sink is disabled")
        if self._file_descriptor is None:
            with self._open_lock:
                if self._file_descriptor is None:
                    self._file_descriptor = os.open(
                        self.path,
                        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                        0o600,
                    )
        return self._file_descriptor

    def emit(self, event: str, request_id: str, **fields: Any) -> None:
        if not self.enabled:
            return
        if not event or not request_id:
            raise ValueError("event and request_id must be nonempty")
        payload = {
            "schema": "vllm-hust.kv-transfer-event.v1",
            "event": event,
            "fields": fields,
            "pid": os.getpid(),
            "request_id": request_id,
            "ts_monotonic_ns": time.monotonic_ns(),
        }
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
        os.write(self._descriptor(), line.encode("utf-8"))

    def close(self) -> None:
        if self._file_descriptor is not None:
            os.close(self._file_descriptor)
            self._file_descriptor = None

    def __enter__(self) -> JsonlKVTransferEventSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["JsonlKVTransferEventSink"]
