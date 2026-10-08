# SPDX-License-Identifier: Apache-2.0
"""Binding from the vLLM-HUST offloading observer to plugin records."""

from __future__ import annotations

import secrets
from threading import Lock
from typing import Any

from .adapter import HOST_OBSERVER_CONTRACT, HostObserverCallbacks
from .normalization import (
    DEFAULT_MAX_CORRELATED_TRANSFERS,
    CoreTransferCancelled,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    TransferOperation,
)
from .schema import (
    UINT32_MAX,
    UINT64_MAX,
    ObservationIdentity,
    ReceiptIdentity,
    TransferIdentity,
    TransferTerminalReason,
)

REQUIRED_HOST_API_VERSION = "1.0"
MAX_BINDING_TRANSFERS = DEFAULT_MAX_CORRELATED_TRANSFERS


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
        # A distinct local incarnation separates worker restarts. This is
        # plugin-owned identity, not a host-provided cross-process generation.
        self._worker_generation = int(self._process_uuid[:16], 16) or 1
        self._callbacks: HostObserverCallbacks | None = None
        self._pending: dict[
            int, tuple[ObservationIdentity, TransferIdentity, TransferOperation]
        ] = {}
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
        with self._state_lock:
            self._callbacks = None
            self._pending.clear()
        self._host_api.unregister_kv_transfer_observer(handle)

    def _identity(self, record: Any) -> ObservationIdentity | None:
        if (
            type(getattr(record, "job_id", None)) is not int
            or not 0 <= record.job_id <= UINT64_MAX
            or type(getattr(record, "rank", None)) is not int
            or not 0 <= record.rank <= UINT32_MAX
            or type(getattr(record, "request_id", None)) is not str
        ):
            return None
        try:
            return ObservationIdentity(
                request_id=record.request_id,
                worker_generation=self._worker_generation,
                rank=record.rank,
            )
        except ValueError:
            return None

    def _transfer(self, job_id: int) -> TransferIdentity:
        return TransferIdentity(f"{self._process_uuid}:t:{job_id}")

    def _operation(self, record: Any) -> TransferOperation | None:
        value = getattr(getattr(record, "operation", None), "value", None)
        try:
            return TransferOperation(value)
        except (TypeError, ValueError):
            return None

    def _observe_host(self, record: Any) -> bool:
        event = getattr(getattr(record, "event", None), "value", None)
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
            try:
                source = CoreTransferSubmitted(
                    identity=identity,
                    transfer=self._transfer(job_id),
                    operation=operation,
                    block_count=record.block_count,
                    observed_at_ns=record.observed_at_ns,
                )
            except (AttributeError, TypeError, ValueError):
                return False
            with self._state_lock:
                callbacks = self._callbacks
                if (
                    callbacks is None
                    or job_id in self._pending
                    or len(self._pending) >= MAX_BINDING_TRANSFERS
                ):
                    return False
                try:
                    accepted = callbacks.observe(source) is True
                except Exception:
                    return False
                if accepted:
                    self._pending[job_id] = (identity, source.transfer, operation)
                return accepted

        if event not in {"transfer_completed", "transfer_cancelled"}:
            return False
        with self._state_lock:
            callbacks = self._callbacks
            pending = self._pending.get(job_id)
            if callbacks is None or pending is None or operation is None:
                return False
            identity, transfer, submitted_operation = pending
            if (
                operation is not submitted_operation
                or getattr(record, "request_id", None) != identity.request_id
                or getattr(record, "rank", None) != identity.rank
            ):
                return False
            try:
                if event == "transfer_completed":
                    receipt = (
                        ReceiptIdentity(f"{self._process_uuid}:k:{job_id}")
                        if operation is TransferOperation.H2D_RESTORE
                        and record.success is True
                        else None
                    )
                    duration_ns = record.duration_ns
                    source = CoreTransferCompleted(
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
                elif getattr(getattr(record, "reason", None), "value", None) == (
                    "host_shutdown"
                ):
                    source = CoreTransferCancelled(
                        transfer=transfer,
                        observed_at_ns=record.observed_at_ns,
                        reason=TransferTerminalReason.HOST_SHUTDOWN,
                    )
                else:
                    return False
            except (AttributeError, TypeError, ValueError):
                return False
            try:
                accepted = callbacks.observe(source) is True
            except Exception:
                accepted = False
            del self._pending[job_id]
            return accepted


__all__ = [
    "MAX_BINDING_TRANSFERS",
    "REQUIRED_HOST_API_VERSION",
    "VllmOffloadingObserverBinding",
]
