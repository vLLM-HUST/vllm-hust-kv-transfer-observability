# SPDX-License-Identifier: Apache-2.0
"""Binding from the vLLM-HUST offloading observer to plugin records."""

from __future__ import annotations

import secrets
from threading import Lock
from typing import Any

from .adapter import HOST_OBSERVER_CONTRACT, HostObserverCallbacks
from .correlated import (
    CorrelatedEvent,
    CorrelatedHostObservation,
    JobReceipt,
    WorkerReceipt,
)
from .host_facts import HostFact, HostFactEvent
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
    ComputeKind,
    ObservationIdentity,
    ReceiptIdentity,
    RecoveryRequeueReason,
    TransferIdentity,
    TransferTerminalReason,
)

REQUIRED_HOST_API_VERSION = "1.0"
CORRELATED_HOST_CONTRACT = "vllm.kv-transfer.observer.v2"
CORRELATED_HOST_API_VERSION = "2.0"
MAX_BINDING_TRANSFERS = DEFAULT_MAX_CORRELATED_TRANSFERS


class VllmOffloadingObserverBinding:
    """Adapt the public host seam without importing plugin types into vLLM."""

    contract_version = HOST_OBSERVER_CONTRACT

    def __init__(
        self, host_api: Any | None = None, correlated_api: Any | None = None
    ) -> None:
        if host_api is None:
            from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
                observability as host_api,
            )

            try:
                from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
                    correlated_observability as correlated_api,
                )
            except ImportError:
                correlated_api = None

        if host_api.KV_TRANSFER_OBSERVER_CONTRACT != HOST_OBSERVER_CONTRACT:
            raise RuntimeError("vLLM-HUST KV transfer observer contract mismatch")
        if host_api.KV_TRANSFER_OBSERVABILITY_API_VERSION != REQUIRED_HOST_API_VERSION:
            raise RuntimeError("unsupported vLLM-HUST KV transfer observer API")
        self._host_api = host_api
        if correlated_api is not None and (
            getattr(correlated_api, "KV_TRANSFER_CORRELATED_OBSERVER_CONTRACT", None)
            != CORRELATED_HOST_CONTRACT
            or getattr(
                correlated_api, "KV_TRANSFER_CORRELATED_OBSERVABILITY_API_VERSION", None
            )
            != CORRELATED_HOST_API_VERSION
        ):
            raise RuntimeError("unsupported correlated host observer API")
        self._correlated_api = correlated_api
        self._correlated_handle: object | None = None
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
        if handle is None:
            raise RuntimeError("host observer returned no handle")
        if self._correlated_api is not None and callbacks.correlated is not None:
            try:
                self._correlated_handle = (
                    self._correlated_api.register_correlated_observer(
                        self._observe_correlated_host
                    )
                )
                if self._correlated_handle is None:
                    raise RuntimeError("correlated observer returned no handle")
            except Exception:
                self._host_api.unregister_kv_transfer_observer(handle)
                raise
        self._callbacks = callbacks
        return handle

    def unregister(self, handle: object) -> None:
        with self._state_lock:
            self._callbacks = None
            self._pending.clear()
        if self._correlated_handle is not None:
            assert self._correlated_api is not None
            try:
                self._correlated_api.unregister_correlated_observer(
                    self._correlated_handle
                )
            finally:
                self._correlated_handle = None
                self._host_api.unregister_kv_transfer_observer(handle)
        else:
            self._host_api.unregister_kv_transfer_observer(handle)

    def _observe_correlated_host(self, record: Any) -> bool:
        callback = self._callbacks.correlated if self._callbacks is not None else None
        if callback is None:
            return False
        try:
            event = CorrelatedEvent(record.event.value)
            workers = tuple(
                WorkerReceipt(item.rank, item.worker_generation)
                for item in record.workers
            )
            roster = tuple(
                JobReceipt(
                    job.job_id,
                    tuple(
                        WorkerReceipt(item.rank, item.worker_generation)
                        for item in job.workers
                    ),
                )
                for job in record.roster
            )
            item = CorrelatedHostObservation(
                event=event,
                scheduler_generation=record.scheduler_generation,
                request_id=record.request_id,
                recovery_epoch=record.recovery_epoch,
                observed_at_ns=record.observed_at_ns,
                job_id=record.job_id,
                rank=record.rank,
                worker_generation=record.worker_generation,
                block_count=record.block_count,
                workers=workers,
                roster=roster,
                compute_kind=record.compute_kind,
            )
            return callback(item) is True
        except Exception:
            return False

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
        if type(event) is not str:
            return False
        try:
            fact_event = HostFactEvent(event)
        except ValueError:
            pass
        else:
            return self._observe_host_fact(record, fact_event)
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

    def _observe_host_fact(self, record: Any, event: HostFactEvent) -> bool:
        callback = self._callbacks.host_fact if self._callbacks is not None else None
        if callback is None:
            return False
        try:
            shared = {
                "event": event,
                "process_uuid": self._process_uuid,
                "observed_at_ns": record.observed_at_ns,
                "request_id": record.request_id,
            }
            if event is HostFactEvent.TRANSFER_RECEIPT:
                if record.success is not True or self._operation(record) is not (
                    TransferOperation.H2D_RESTORE
                ):
                    return False
                fact = HostFact(
                    **shared,
                    job_id=record.job_id,
                    rank=record.rank,
                    ranks=record.ranks,
                )
            elif event is HostFactEvent.RECOVERY_REQUEUED:
                fact = HostFact(
                    **shared,
                    recovery_epoch=record.recovery_epoch,
                    requeue_reason=RecoveryRequeueReason(record.requeue_reason.value),
                )
            elif event is HostFactEvent.RECOVERY_ADMITTED:
                fact = HostFact(
                    **shared,
                    recovery_epoch=record.recovery_epoch,
                    job_ids=record.job_ids,
                )
            else:
                fact = HostFact(
                    **shared,
                    rank=record.rank,
                    recovery_epoch=record.recovery_epoch,
                    job_ids=record.job_ids,
                    compute_kind=ComputeKind(record.compute_kind.value),
                )
            return callback(fact) is True
        except (AttributeError, TypeError, ValueError):
            return False
        except Exception:
            return False


__all__ = [
    "CORRELATED_HOST_API_VERSION",
    "CORRELATED_HOST_CONTRACT",
    "MAX_BINDING_TRANSFERS",
    "REQUIRED_HOST_API_VERSION",
    "VllmOffloadingObserverBinding",
]
