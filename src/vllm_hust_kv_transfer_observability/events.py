# SPDX-License-Identifier: Apache-2.0
"""Process-safe JSONL event sink extracted from legacy B134 instrumentation."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# Normative adapter vocabulary (HOST_CONTRACT.md, "Event ownership boundary").
# Scheduler-layer events (preempt/wakeup/admission/scheduled) are owned by the
# host EventBus (vllm-hust#6) and are intentionally NOT part of this package's
# sink vocabulary. Keep in sync with HOST_CONTRACT.md and
# tools/e2e_910b/verify_events.py.
EVENT_VOCABULARY_V1: frozenset[str] = frozenset(
    {
        "restore_start",
        "restore_done",
        "cpu_store",
        "cpu_evict",
        "evict",
        "transfer_submit",
        "swap_d2h_submit",
        "gather_h2d",
        "copy_observed_complete",
        "sched_step",
    }
)

# Address-like field names are rejected on the event path so no process/device
# address material can reach disk, mirroring the descriptor red line.
# Substring match keeps variants (src_address, data_ptr, device_ptr, ...) out.
_ADDRESS_LIKE_PARTS = ("address", "addr", "ptr", "pointer")


def _is_address_like_field(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _ADDRESS_LIKE_PARTS)


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

    def emit(
        self,
        event: str,
        request_id: str,
        *,
        transfer_id: str | None = None,
        recovery_epoch: int | None = None,
        rank: int | None = None,
        generation: int | None = None,
        **fields: Any,
    ) -> None:
        """Append one event when a path is configured (default-off).

        Bounded identity: ``request_id`` always denotes the request; transfer
        jobs, recovery epochs, ranks and worker generations use their own
        optional fields (never encoded into ``request_id``), per
        HOST_CONTRACT.md identity.v1. Unknown event names and address-like
        field names are rejected (fail-closed).
        """
        if not self.enabled:
            return
        if not event or not request_id:
            raise ValueError("event and request_id must be nonempty")
        if event not in EVENT_VOCABULARY_V1:
            raise ValueError(
                f"event {event!r} is outside the v1 vocabulary "
                "(HOST_CONTRACT.md event ownership boundary)"
            )
        address_like = sorted(
            field for field in fields if _is_address_like_field(field)
        )
        if address_like:
            raise ValueError(
                f"address-like field(s) rejected: {', '.join(address_like)}"
            )
        if transfer_id is not None and not transfer_id:
            raise ValueError("transfer_id must be nonempty when provided")
        payload: dict[str, Any] = {
            "schema": "vllm-hust.kv-transfer-event.v1",
            "event": event,
            "fields": fields,
            "pid": os.getpid(),
            "request_id": request_id,
            "ts_monotonic_ns": time.monotonic_ns(),
        }
        if transfer_id is not None:
            payload["transfer_id"] = transfer_id
        if recovery_epoch is not None:
            payload["recovery_epoch"] = recovery_epoch
        if rank is not None:
            payload["rank"] = rank
        if generation is not None:
            payload["generation"] = generation
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


__all__ = ["EVENT_VOCABULARY_V1", "JsonlKVTransferEventSink"]
