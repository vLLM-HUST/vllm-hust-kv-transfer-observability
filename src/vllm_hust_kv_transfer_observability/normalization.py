# SPDX-License-Identifier: Apache-2.0
"""Bounded normalization of legacy core and Ascend lifecycle observations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Lock

from .schema import (
    MAX_TRANSFER_ASSOCIATIONS,
    UINT32_MAX,
    UINT64_MAX,
    ComputeKind,
    KVTransferObservation,
    ObservationEvent,
    ObservationIdentity,
    ReceiptIdentity,
    RecoveryRequeueReason,
    TransferDirection,
    TransferIdentity,
    TransferTerminalReason,
)

DEFAULT_MAX_CORRELATED_TRANSFERS = 4096
DEFAULT_MAX_RECOVERY_ADMISSIONS = 4096


class SourceHost(str, Enum):
    """Host family that forwarded an otherwise identical observation."""

    VLLM = "vllm"
    VLLM_ASCEND = "vllm-ascend"


class TransferOperation(str, Enum):
    """Operations exposed by the merged core KV-recovery seam."""

    D2H_PRESERVE = "d2h_preserve"
    H2D_RESTORE = "h2d_restore"

    @property
    def direction(self) -> TransferDirection:
        if self is TransferOperation.D2H_PRESERVE:
            return TransferDirection.D2H
        return TransferDirection.H2D


def _require_uint64(value: object, name: str) -> None:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise ValueError(f"{name} must be a uint64")


def _require_positive_uint(value: object, name: str, maximum: int = UINT64_MAX) -> None:
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError(f"{name} must be a positive integer <= {maximum}")


@dataclass(frozen=True, slots=True)
class CoreTransferSubmitted:
    """Core #236 transfer attempt after backend submission succeeds."""

    identity: ObservationIdentity
    transfer: TransferIdentity
    operation: TransferOperation
    block_count: int
    observed_at_ns: int

    def __post_init__(self) -> None:
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        if type(self.transfer) is not TransferIdentity:
            raise ValueError("transfer must be a TransferIdentity")
        if type(self.operation) is not TransferOperation:
            raise ValueError("operation must be a TransferOperation")
        _require_positive_uint(self.block_count, "block_count", UINT32_MAX)
        _require_uint64(self.observed_at_ns, "observed_at_ns")


@dataclass(frozen=True, slots=True)
class CoreTransferCompleted:
    """Core #236 completion measurement for one submitted transfer."""

    transfer: TransferIdentity
    observed_at_ns: int
    success: bool
    bytes_moved: int | None = None
    device_duration_ns: int | None = None
    receipt: ReceiptIdentity | None = None

    def __post_init__(self) -> None:
        if type(self.transfer) is not TransferIdentity:
            raise ValueError("transfer must be a TransferIdentity")
        _require_uint64(self.observed_at_ns, "observed_at_ns")
        if type(self.success) is not bool:
            raise ValueError("success must be a bool")
        if self.bytes_moved is not None:
            _require_positive_uint(self.bytes_moved, "bytes_moved")
        if self.device_duration_ns is not None:
            _require_positive_uint(self.device_duration_ns, "device_duration_ns")
        if self.receipt is not None and type(self.receipt) is not ReceiptIdentity:
            raise ValueError("receipt must be a ReceiptIdentity")


@dataclass(frozen=True, slots=True)
class CoreTransferCancelled:
    """Explicit discard handoff for a transfer that will not complete."""

    transfer: TransferIdentity
    observed_at_ns: int
    reason: TransferTerminalReason

    def __post_init__(self) -> None:
        if type(self.transfer) is not TransferIdentity:
            raise ValueError("transfer must be a TransferIdentity")
        _require_uint64(self.observed_at_ns, "observed_at_ns")
        if self.reason not in {
            TransferTerminalReason.REQUEST_CANCELLED,
            TransferTerminalReason.WORKER_GENERATION_CHANGED,
            TransferTerminalReason.HOST_SHUTDOWN,
        }:
            raise ValueError("cancellation requires a cancellation reason")


@dataclass(frozen=True, slots=True)
class CoreRecoveryRequeued:
    identity: ObservationIdentity
    reason: RecoveryRequeueReason
    observed_at_ns: int

    def __post_init__(self) -> None:
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        if type(self.reason) is not RecoveryRequeueReason:
            raise ValueError("reason must be a RecoveryRequeueReason")
        _require_uint64(self.observed_at_ns, "observed_at_ns")


@dataclass(frozen=True, slots=True)
class CoreRecoveryAdmitted:
    identity: ObservationIdentity
    transfers: tuple[TransferIdentity, ...]
    observed_at_ns: int

    def __post_init__(self) -> None:
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        if type(self.transfers) is not tuple or not self.transfers:
            raise ValueError("transfers must be a nonempty tuple")
        if any(type(item) is not TransferIdentity for item in self.transfers):
            raise ValueError("transfers contains an invalid identity")
        if tuple(sorted(set(self.transfers))) != self.transfers:
            raise ValueError("transfers must be sorted and unique")
        if len(self.transfers) > MAX_TRANSFER_ASSOCIATIONS:
            raise ValueError("transfers exceeds the bounded association limit")
        _require_uint64(self.observed_at_ns, "observed_at_ns")


@dataclass(frozen=True, slots=True)
class FirstComputeObserved:
    """Exact recovered roster observed immediately before model forward."""

    source: SourceHost
    identity: ObservationIdentity
    receipt: ReceiptIdentity
    transfers: tuple[TransferIdentity, ...]
    compute_kind: ComputeKind
    observed_at_ns: int

    def __post_init__(self) -> None:
        if type(self.source) is not SourceHost:
            raise ValueError("source must be a SourceHost")
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        if type(self.receipt) is not ReceiptIdentity:
            raise ValueError("receipt must be a ReceiptIdentity")
        if type(self.transfers) is not tuple or not self.transfers:
            raise ValueError("transfers must be a nonempty tuple")
        if any(type(item) is not TransferIdentity for item in self.transfers):
            raise ValueError("transfers contains an invalid identity")
        if tuple(sorted(set(self.transfers))) != self.transfers:
            raise ValueError("transfers must be sorted and unique")
        if len(self.transfers) > MAX_TRANSFER_ASSOCIATIONS:
            raise ValueError("transfers exceeds the bounded association limit")
        if type(self.compute_kind) is not ComputeKind:
            raise ValueError("compute_kind must be a ComputeKind")
        _require_uint64(self.observed_at_ns, "observed_at_ns")


SourceObservation = (
    CoreTransferSubmitted
    | CoreTransferCompleted
    | CoreTransferCancelled
    | CoreRecoveryRequeued
    | CoreRecoveryAdmitted
    | FirstComputeObserved
)


@dataclass(frozen=True, slots=True)
class NormalizationCounters:
    accepted: int = 0
    invalid: int = 0
    capacity_dropped: int = 0
    evidence_disabled_dropped: int = 0
    closed_dropped: int = 0


@dataclass(frozen=True, slots=True)
class _CompletedRestore:
    identity: ObservationIdentity
    receipt: ReceiptIdentity
    observed_at_ns: int


@dataclass(frozen=True, slots=True)
class _RecoveryAdmission:
    transfers: tuple[TransferIdentity, ...]
    observed_at_ns: int


class LifecycleNormalizer:
    """Normalize typed host observations without owning host execution.

    Invalid ordering or identity returns ``None`` and is counted. Capacity and
    exact-roster failures disable further formal evidence while never raising
    into the caller. A later host adapter is responsible for constructing the
    typed source records and forwarding returned canonical observations.
    """

    def __init__(
        self,
        *,
        max_correlated_transfers: int = DEFAULT_MAX_CORRELATED_TRANSFERS,
        max_recovery_admissions: int = DEFAULT_MAX_RECOVERY_ADMISSIONS,
    ) -> None:
        for value, name in (
            (max_correlated_transfers, "max_correlated_transfers"),
            (max_recovery_admissions, "max_recovery_admissions"),
        ):
            if type(value) is not int or not 0 < value <= MAX_TRANSFER_ASSOCIATIONS:
                raise ValueError(
                    f"{name} must be within 1..{MAX_TRANSFER_ASSOCIATIONS}"
                )
        self.max_correlated_transfers = max_correlated_transfers
        self.max_recovery_admissions = max_recovery_admissions
        self._pending: dict[TransferIdentity, CoreTransferSubmitted] = {}
        self._completed_restores: dict[TransferIdentity, _CompletedRestore] = {}
        self._admissions: dict[ObservationIdentity, _RecoveryAdmission] = {}
        self._counter_values = {field: 0 for field in NormalizationCounters.__slots__}
        self._evidence_valid = True
        self._closed = False
        self._lock = Lock()

    @property
    def counters(self) -> NormalizationCounters:
        with self._lock:
            return NormalizationCounters(**self._counter_values)

    @property
    def evidence_valid(self) -> bool:
        with self._lock:
            return self._evidence_valid

    def normalize(self, source: SourceObservation) -> KVTransferObservation | None:
        with self._lock:
            if self._closed:
                self._counter_values["closed_dropped"] += 1
                return None
            if not self._evidence_valid:
                self._counter_values["evidence_disabled_dropped"] += 1
                return None
            try:
                result = self._normalize_locked(source)
            except (TypeError, ValueError):
                self._counter_values["invalid"] += 1
                return None
            if result is not None:
                self._counter_values["accepted"] += 1
            return result

    def _disable_for_capacity(self) -> None:
        self._evidence_valid = False
        self._counter_values["capacity_dropped"] += 1

    def _normalize_locked(
        self, source: SourceObservation
    ) -> KVTransferObservation | None:
        if type(source) is CoreTransferSubmitted:
            return self._transfer_submitted(source)
        if type(source) is CoreTransferCompleted:
            return self._transfer_completed(source)
        if type(source) is CoreTransferCancelled:
            return self._transfer_cancelled(source)
        if type(source) is CoreRecoveryRequeued:
            return KVTransferObservation(
                ObservationEvent.RECOVERY_REQUEUED,
                source.identity,
                observed_at_ns=source.observed_at_ns,
                requeue_reason=source.reason,
            )
        if type(source) is CoreRecoveryAdmitted:
            return self._recovery_admitted(source)
        if type(source) is FirstComputeObserved:
            return self._first_compute(source)
        raise TypeError("unsupported source observation")

    def _transfer_submitted(
        self, source: CoreTransferSubmitted
    ) -> KVTransferObservation | None:
        if (
            source.transfer in self._pending
            or source.transfer in self._completed_restores
        ):
            raise ValueError("duplicate transfer identity")
        if len(self._pending) >= self.max_correlated_transfers:
            self._disable_for_capacity()
            return None
        self._pending[source.transfer] = source
        event = (
            ObservationEvent.PRESERVE_STARTED
            if source.operation is TransferOperation.D2H_PRESERVE
            else ObservationEvent.RESTORE_STARTED
        )
        return KVTransferObservation(
            event,
            source.identity,
            observed_at_ns=source.observed_at_ns,
            transfer=source.transfer,
            direction=source.operation.direction,
            block_count=source.block_count,
        )

    def _transfer_completed(
        self, source: CoreTransferCompleted
    ) -> KVTransferObservation | None:
        pending = self._pending.get(source.transfer)
        if pending is None or source.observed_at_ns < pending.observed_at_ns:
            raise ValueError("completion does not match a submitted transfer")
        if (
            pending.operation is TransferOperation.D2H_PRESERVE
            and source.observed_at_ns == pending.observed_at_ns
        ):
            raise ValueError("D2H completion must follow submission")
        if not source.success:
            if source.bytes_moved is not None or source.receipt is not None:
                raise ValueError("failed completion cannot carry success evidence")
            del self._pending[source.transfer]
            return KVTransferObservation(
                ObservationEvent.TRANSFER_FAILED,
                pending.identity,
                observed_at_ns=source.observed_at_ns,
                transfer=source.transfer,
                direction=pending.operation.direction,
                terminal_reason=TransferTerminalReason.BACKEND_FAILURE,
            )
        if source.bytes_moved is None:
            raise ValueError("successful completion requires bytes_moved")
        duration_ns = source.observed_at_ns - pending.observed_at_ns
        if pending.operation is TransferOperation.H2D_RESTORE:
            if duration_ns == 0 or source.receipt is None:
                raise ValueError("successful restore requires a later receipt")
            if len(self._completed_restores) >= self.max_correlated_transfers:
                del self._pending[source.transfer]
                self._disable_for_capacity()
                return None
            result = KVTransferObservation(
                ObservationEvent.RESTORE_COMPLETED,
                pending.identity,
                observed_at_ns=source.observed_at_ns,
                transfer=source.transfer,
                direction=TransferDirection.H2D,
                receipt=source.receipt,
                bytes_moved=source.bytes_moved,
                block_count=pending.block_count,
                duration_ns=duration_ns,
                device_duration_ns=source.device_duration_ns,
            )
            self._completed_restores[source.transfer] = _CompletedRestore(
                pending.identity, source.receipt, source.observed_at_ns
            )
        else:
            if duration_ns == 0 or source.receipt is not None:
                raise ValueError("preserve completion has invalid receipt or timing")
            result = KVTransferObservation(
                ObservationEvent.PRESERVE_COMPLETED,
                pending.identity,
                observed_at_ns=source.observed_at_ns,
                transfer=source.transfer,
                direction=TransferDirection.D2H,
                bytes_moved=source.bytes_moved,
                block_count=pending.block_count,
                duration_ns=duration_ns,
                device_duration_ns=source.device_duration_ns,
            )
        del self._pending[source.transfer]
        return result

    def _transfer_cancelled(
        self, source: CoreTransferCancelled
    ) -> KVTransferObservation:
        pending = self._pending.get(source.transfer)
        if pending is None or source.observed_at_ns < pending.observed_at_ns:
            raise ValueError("cancellation does not match a submitted transfer")
        del self._pending[source.transfer]
        return KVTransferObservation(
            ObservationEvent.TRANSFER_CANCELLED,
            pending.identity,
            observed_at_ns=source.observed_at_ns,
            transfer=source.transfer,
            direction=pending.operation.direction,
            terminal_reason=source.reason,
        )

    def _recovery_admitted(
        self, source: CoreRecoveryAdmitted
    ) -> KVTransferObservation | None:
        if source.identity.recovery_epoch is None:
            raise ValueError("recovery admission requires an epoch")
        if source.identity in self._admissions:
            raise ValueError("duplicate recovery admission")
        if len(self._admissions) >= self.max_recovery_admissions:
            self._disable_for_capacity()
            return None
        for transfer in source.transfers:
            completed = self._completed_restores.get(transfer)
            if (
                completed is None
                or completed.identity != source.identity
                or completed.observed_at_ns > source.observed_at_ns
            ):
                raise ValueError("admission roster lacks an exact restore receipt")
        self._admissions[source.identity] = _RecoveryAdmission(
            source.transfers, source.observed_at_ns
        )
        return KVTransferObservation(
            ObservationEvent.RECOVERY_ADMITTED,
            source.identity,
            observed_at_ns=source.observed_at_ns,
            associated_transfers=source.transfers,
        )

    def _first_compute(self, source: FirstComputeObserved) -> KVTransferObservation:
        admitted = self._admissions.get(source.identity)
        if (
            admitted is None
            or admitted.transfers != source.transfers
            or source.observed_at_ns < admitted.observed_at_ns
            or any(
                transfer.process_uuid != source.receipt.process_uuid
                for transfer in source.transfers
            )
        ):
            self._evidence_valid = False
            raise ValueError("first-compute roster does not match admission")
        result = KVTransferObservation(
            ObservationEvent.FIRST_COMPUTE,
            source.identity,
            observed_at_ns=source.observed_at_ns,
            receipt=source.receipt,
            associated_transfers=source.transfers,
            compute_kind=source.compute_kind,
        )
        del self._admissions[source.identity]
        for transfer in source.transfers:
            self._completed_restores.pop(transfer, None)
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            self._completed_restores.clear()
            self._admissions.clear()


__all__ = [
    "CoreRecoveryAdmitted",
    "CoreRecoveryRequeued",
    "CoreTransferCancelled",
    "CoreTransferCompleted",
    "CoreTransferSubmitted",
    "DEFAULT_MAX_CORRELATED_TRANSFERS",
    "DEFAULT_MAX_RECOVERY_ADMISSIONS",
    "FirstComputeObserved",
    "LifecycleNormalizer",
    "NormalizationCounters",
    "SourceHost",
    "SourceObservation",
    "TransferOperation",
]
