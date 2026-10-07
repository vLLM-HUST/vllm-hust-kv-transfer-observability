# SPDX-License-Identifier: Apache-2.0
"""Binding from the vLLM-HUST offloading observer to plugin records."""

from __future__ import annotations

import secrets
from threading import Lock
from typing import Any

from .adapter import HOST_OBSERVER_CONTRACT, HostObserverCallbacks
from .normalization import (
    CoreTransferCancelled,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    TransferOperation,
)
from .schema import (
    ObservationIdentity,
    ReceiptIdentity,
    TransferIdentity,
    TransferTerminalReason,
)

REQUIRED_HOST_API_VERSION = "1.0"


class VllmOffloadingObserverBinding:
    """Adapt the public host seam without importing plugin types into vLLM."""

    contract_version = HOST_OBSERVER_CONTRACT

    def __init__(self, host_api: Any | None = None) -> None:
        if host_api is None:
            from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
                observability as host_api,
            )

        if host_api.KV_TRANSFER_OBSERVER_CONTRACT != HOST_OBSERVER_CONTRACT:
            raise RuntimeError("vLLM-HUST KV transfer observer contract mismatch")
        if host_api.KV_TRANSFER_OBSERVABILITY_API_VERSION != REQUIRED_HOST_API_VERSION:
            raise RuntimeError("unsupported vLLM-HUST KV transfer observer API")
        self._host_api = host_api
        self._process_uuid = secrets.token_hex(16)
        self._callbacks: HostObserverCallbacks | None = None
        self._identities: dict[int, ObservationIdentity] = {}
        self._transfers: dict[int, TransferIdentity] = {}
        self._state_lock = Lock()

    def register(self, callbacks: HostObserverCallbacks) -> object:
        if self._callbacks is not None:
            raise RuntimeError("host observer binding is already registered")
        handle = self._host_api.register_kv_transfer_observer(
            "kv_transfer_observability", self._observe_host
        )
        self._callbacks = callbacks
        return handle

    def unregister(self, handle: object) -> None:
        self._host_api.unregister_kv_transfer_observer(handle)
        self._callbacks = None
        with self._state_lock:
            self._identities.clear()
            self._transfers.clear()

    def _identity(self, record: Any) -> ObservationIdentity | None:
        if (
            type(getattr(record, "job_id", None)) is not int
            or record.job_id < 0
            or type(getattr(record, "rank", None)) is not int
            or record.rank < 0
            or type(getattr(record, "request_id", None)) is not str
        ):
            return None
        return ObservationIdentity(
            request_id=record.request_id,
            worker_generation=0,
            rank=record.rank,
        )

    def _transfer(self, job_id: int) -> TransferIdentity:
        return TransferIdentity(f"{self._process_uuid}:t:{job_id}")

    def _operation(self, record: Any) -> TransferOperation | None:
        value = getattr(record.operation, "value", None)
        try:
            return TransferOperation(value)
        except (TypeError, ValueError):
            return None

    def _observe_host(self, record: Any) -> bool:
        callbacks = self._callbacks
        if callbacks is None:
            return False
        event = getattr(record.event, "value", None)
        job_id = getattr(record, "job_id", None)
        operation = self._operation(record)

        if event == "transfer_submitted":
            identity = self._identity(record)
            if (
                identity is None
                or operation is None
                or type(getattr(record, "block_count", None)) is not int
            ):
                return False
            transfer = self._transfer(job_id)
            with self._state_lock:
                self._identities[job_id] = identity
                self._transfers[job_id] = transfer
            return callbacks.observe(
                CoreTransferSubmitted(
                    identity=identity,
                    transfer=transfer,
                    operation=operation,
                    block_count=record.block_count,
                    observed_at_ns=record.observed_at_ns,
                )
            )

        if event not in {"transfer_completed", "transfer_cancelled"}:
            return False
        with self._state_lock:
            identity = self._identities.pop(job_id, None)
            transfer = self._transfers.pop(job_id, None)
        if identity is None or transfer is None or operation is None:
            return False

        if event == "transfer_completed":
            receipt = (
                ReceiptIdentity(f"{self._process_uuid}:k:{job_id}")
                if operation is TransferOperation.H2D_RESTORE and record.success is True
                else None
            )
            duration_ns = record.duration_ns
            accepted = callbacks.observe(
                CoreTransferCompleted(
                    transfer=transfer,
                    observed_at_ns=record.observed_at_ns,
                    success=record.success,
                    bytes_moved=record.bytes_moved,
                    device_duration_ns=(
                        duration_ns
                        if type(duration_ns) is int and duration_ns > 0
                        else None
                    ),
                    receipt=receipt,
                )
            )
        elif getattr(record.reason, "value", None) == "host_shutdown":
            accepted = callbacks.observe(
                CoreTransferCancelled(
                    transfer=transfer,
                    observed_at_ns=record.observed_at_ns,
                    reason=TransferTerminalReason.HOST_SHUTDOWN,
                )
            )
        else:
            return False
        return accepted


__all__ = ["REQUIRED_HOST_API_VERSION", "VllmOffloadingObserverBinding"]
