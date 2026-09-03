# SPDX-License-Identifier: Apache-2.0
"""Typed, bounded, address-free KV lifecycle observation records."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

MAX_RUNTIME_REQUEST_ID_BYTES: Final = 128
MAX_TRANSFER_ASSOCIATIONS: Final = 4096
UINT32_MAX: Final = 2**32 - 1
UINT64_MAX: Final = 2**64 - 1

_PROCESS_SCOPED_ID = re.compile(
    r"^(?P<process>[0-9a-f]{32}):(?P<kind>[tk]):(?P<seq>0|[1-9][0-9]{0,19})$"
)


class ObservationEvent(str, Enum):
    """Closed event vocabulary requested by the host-contract proposal."""

    PRESERVE_STARTED = "preserve_started"
    PRESERVE_COMPLETED = "preserve_completed"
    TRANSFER_STARTED = "transfer_started"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_FAILED = "transfer_failed"
    TRANSFER_CANCELLED = "transfer_cancelled"
    RESTORE_STARTED = "restore_started"
    RESTORE_COMPLETED = "restore_completed"
    RECOVERY_REQUEUED = "recovery_requeued"
    RECOVERY_ADMITTED = "recovery_admitted"
    FIRST_COMPUTE = "first_compute"


class TransferDirection(str, Enum):
    D2H = "d2h"
    H2D = "h2d"


class ComputeKind(str, Enum):
    PREFILL = "prefill"
    DECODE = "decode"


class RecoveryRequeueReason(str, Enum):
    LORA_CAPACITY = "lora_capacity"
    PREFILL_THROTTLED = "prefill_throttled"
    TOKEN_BUDGET = "token_budget"
    ENCODER_BUDGET = "encoder_budget"
    BLOCK_CAPACITY = "block_capacity"
    UNCLASSIFIED = "unclassified"


class TransferTerminalReason(str, Enum):
    SUBMISSION_REJECTED = "submission_rejected"
    BACKEND_FAILURE = "backend_failure"
    INVALID_MEASUREMENT = "invalid_measurement"
    REQUEST_CANCELLED = "request_cancelled"
    WORKER_GENERATION_CHANGED = "worker_generation_changed"
    HOST_SHUTDOWN = "host_shutdown"


_FAILURE_REASONS = {
    TransferTerminalReason.SUBMISSION_REJECTED,
    TransferTerminalReason.BACKEND_FAILURE,
    TransferTerminalReason.INVALID_MEASUREMENT,
}
_CANCELLATION_REASONS = {
    TransferTerminalReason.REQUEST_CANCELLED,
    TransferTerminalReason.WORKER_GENERATION_CHANGED,
    TransferTerminalReason.HOST_SHUTDOWN,
}


def _require_uint(value: object, field_name: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{field_name} must be an unsigned integer <= {maximum}")
    return value


def _require_positive_uint(value: object, field_name: str, maximum: int) -> int:
    result = _require_uint(value, field_name, maximum)
    if result == 0:
        raise ValueError(f"{field_name} must be positive")
    return result


def _require_printable_ascii(value: object, field_name: str, max_bytes: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be ASCII") from exc
    if not encoded or len(encoded) > max_bytes or not value.isprintable():
        raise ValueError(
            f"{field_name} must be nonempty printable ASCII within {max_bytes} bytes"
        )
    return value


def _require_scoped_id(value: object, field_name: str, kind: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    match = _PROCESS_SCOPED_ID.fullmatch(value)
    if (
        match is None
        or match.group("kind") != kind
        or int(match.group("seq")) > UINT64_MAX
    ):
        label = "process_uuid:t:sequence" if kind == "t" else "process_uuid:k:sequence"
        raise ValueError(f"{field_name} must use {label}")
    return value


@dataclass(frozen=True, slots=True)
class ObservationIdentity:
    """Bounded request and worker generation identity."""

    request_id: str
    worker_generation: int
    rank: int
    recovery_epoch: int | None = None

    def __post_init__(self) -> None:
        _require_printable_ascii(
            self.request_id, "request_id", MAX_RUNTIME_REQUEST_ID_BYTES
        )
        _require_uint(self.worker_generation, "worker_generation", UINT64_MAX)
        _require_uint(self.rank, "rank", UINT32_MAX)
        if self.recovery_epoch is not None:
            _require_positive_uint(self.recovery_epoch, "recovery_epoch", UINT64_MAX)

    def to_payload(self) -> dict[str, int | str | None]:
        return {
            "rank": self.rank,
            "recovery_epoch": self.recovery_epoch,
            "request_id": self.request_id,
            "worker_generation": self.worker_generation,
        }


@dataclass(frozen=True, slots=True, order=True)
class TransferIdentity:
    """Process-scoped transfer identity from the merged recovery source."""

    value: str

    def __post_init__(self) -> None:
        _require_scoped_id(self.value, "transfer_id", "t")

    @property
    def process_uuid(self) -> str:
        return self.value.partition(":")[0]


@dataclass(frozen=True, slots=True, order=True)
class ReceiptIdentity:
    """Process-scoped profile/receipt record identity."""

    value: str

    def __post_init__(self) -> None:
        _require_scoped_id(self.value, "receipt_id", "k")

    @property
    def process_uuid(self) -> str:
        return self.value.partition(":")[0]


@dataclass(frozen=True, slots=True)
class KVTransferObservation:
    """One immutable record with event-specific field combinations."""

    event: ObservationEvent
    identity: ObservationIdentity
    observed_at_ns: int = field(default_factory=time.monotonic_ns)
    transfer: TransferIdentity | None = None
    direction: TransferDirection | None = None
    receipt: ReceiptIdentity | None = None
    associated_transfers: tuple[TransferIdentity, ...] = ()
    compute_kind: ComputeKind | None = None
    requeue_reason: RecoveryRequeueReason | None = None
    terminal_reason: TransferTerminalReason | None = None
    bytes_moved: int | None = None
    block_count: int | None = None
    duration_ns: int | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not ObservationEvent:
            raise ValueError("event must be an ObservationEvent")
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        _require_uint(self.observed_at_ns, "observed_at_ns", UINT64_MAX)
        if self.transfer is not None and type(self.transfer) is not TransferIdentity:
            raise ValueError("transfer must be a TransferIdentity")
        if self.direction is not None and type(self.direction) is not TransferDirection:
            raise ValueError("direction must be a TransferDirection")
        if self.receipt is not None and type(self.receipt) is not ReceiptIdentity:
            raise ValueError("receipt must be a ReceiptIdentity")
        if type(self.associated_transfers) is not tuple:
            raise ValueError("associated_transfers must be a tuple")
        if len(self.associated_transfers) > MAX_TRANSFER_ASSOCIATIONS:
            raise ValueError("associated_transfers exceeds the bounded limit")
        if any(
            type(item) is not TransferIdentity for item in self.associated_transfers
        ):
            raise ValueError("associated_transfers contains an invalid identity")
        if tuple(sorted(set(self.associated_transfers))) != self.associated_transfers:
            raise ValueError("associated_transfers must be sorted and unique")
        if self.compute_kind is not None and type(self.compute_kind) is not ComputeKind:
            raise ValueError("compute_kind must be a ComputeKind")
        if (
            self.requeue_reason is not None
            and type(self.requeue_reason) is not RecoveryRequeueReason
        ):
            raise ValueError("requeue_reason must be a RecoveryRequeueReason")
        if (
            self.terminal_reason is not None
            and type(self.terminal_reason) is not TransferTerminalReason
        ):
            raise ValueError("terminal_reason must be a TransferTerminalReason")
        if self.bytes_moved is not None:
            _require_positive_uint(self.bytes_moved, "bytes_moved", UINT64_MAX)
        if self.block_count is not None:
            _require_positive_uint(self.block_count, "block_count", UINT32_MAX)
        if self.duration_ns is not None:
            _require_uint(self.duration_ns, "duration_ns", UINT64_MAX)
        self._validate_event_shape()

    def _validate_event_shape(self) -> None:
        populated = {
            name
            for name in (
                "transfer",
                "direction",
                "receipt",
                "associated_transfers",
                "compute_kind",
                "requeue_reason",
                "terminal_reason",
                "bytes_moved",
                "block_count",
                "duration_ns",
            )
            if getattr(self, name) not in (None, ())
        }
        shapes = {
            ObservationEvent.PRESERVE_STARTED: {
                "transfer",
                "direction",
                "block_count",
            },
            ObservationEvent.PRESERVE_COMPLETED: {
                "transfer",
                "direction",
                "bytes_moved",
                "block_count",
                "duration_ns",
            },
            ObservationEvent.TRANSFER_STARTED: {
                "transfer",
                "direction",
                "bytes_moved",
            },
            ObservationEvent.TRANSFER_COMPLETED: {
                "transfer",
                "direction",
                "bytes_moved",
                "duration_ns",
            },
            ObservationEvent.TRANSFER_FAILED: {
                "transfer",
                "direction",
                "terminal_reason",
            },
            ObservationEvent.TRANSFER_CANCELLED: {
                "transfer",
                "direction",
                "terminal_reason",
            },
            ObservationEvent.RESTORE_STARTED: {
                "transfer",
                "direction",
                "block_count",
            },
            ObservationEvent.RESTORE_COMPLETED: {
                "transfer",
                "direction",
                "receipt",
                "bytes_moved",
                "block_count",
                "duration_ns",
            },
            ObservationEvent.RECOVERY_REQUEUED: {"requeue_reason"},
            ObservationEvent.RECOVERY_ADMITTED: {"associated_transfers"},
            ObservationEvent.FIRST_COMPUTE: {
                "receipt",
                "associated_transfers",
                "compute_kind",
            },
        }
        if populated != shapes[self.event]:
            raise ValueError(
                f"fields do not match the closed schema for {self.event.value}"
            )
        if (
            self.event
            in {
                ObservationEvent.PRESERVE_STARTED,
                ObservationEvent.PRESERVE_COMPLETED,
            }
            and self.direction is not TransferDirection.D2H
        ):
            raise ValueError("preserve events require d2h direction")
        if (
            self.event
            in {
                ObservationEvent.RESTORE_STARTED,
                ObservationEvent.RESTORE_COMPLETED,
            }
            and self.direction is not TransferDirection.H2D
        ):
            raise ValueError("restore events require h2d direction")
        if (
            self.event is ObservationEvent.RESTORE_COMPLETED
            and self.receipt is not None
            and self.transfer is not None
            and self.receipt.process_uuid != self.transfer.process_uuid
        ):
            raise ValueError("restore receipt and transfer must share process scope")
        if (
            self.event is ObservationEvent.TRANSFER_FAILED
            and self.terminal_reason not in _FAILURE_REASONS
        ):
            raise ValueError("transfer_failed has an invalid terminal reason")
        if (
            self.event is ObservationEvent.TRANSFER_CANCELLED
            and self.terminal_reason not in _CANCELLATION_REASONS
        ):
            raise ValueError("transfer_cancelled has an invalid terminal reason")
        if (
            self.event
            in {
                ObservationEvent.RECOVERY_REQUEUED,
                ObservationEvent.RECOVERY_ADMITTED,
                ObservationEvent.FIRST_COMPUTE,
            }
            and self.identity.recovery_epoch is None
        ):
            raise ValueError(f"{self.event.value} requires a recovery epoch")
        if (
            self.event is ObservationEvent.FIRST_COMPUTE
            and self.receipt is not None
            and any(
                transfer.process_uuid != self.receipt.process_uuid
                for transfer in self.associated_transfers
            )
        ):
            raise ValueError(
                "first-compute receipt and transfers must share process scope"
            )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "vllm-hust.kv-transfer-event.v2",
            "event": self.event.value,
            "identity": self.identity.to_payload(),
            "observed_at_ns": self.observed_at_ns,
        }
        optional = {
            "transfer_id": self.transfer.value if self.transfer else None,
            "direction": self.direction.value if self.direction else None,
            "receipt_id": self.receipt.value if self.receipt else None,
            "associated_transfer_ids": [
                item.value for item in self.associated_transfers
            ]
            if self.associated_transfers
            else None,
            "compute_kind": self.compute_kind.value if self.compute_kind else None,
            "requeue_reason": self.requeue_reason.value
            if self.requeue_reason
            else None,
            "terminal_reason": self.terminal_reason.value
            if self.terminal_reason
            else None,
            "bytes_moved": self.bytes_moved,
            "block_count": self.block_count,
            "duration_ns": self.duration_ns,
        }
        payload.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        return payload


__all__ = [
    "ComputeKind",
    "KVTransferObservation",
    "MAX_RUNTIME_REQUEST_ID_BYTES",
    "MAX_TRANSFER_ASSOCIATIONS",
    "ObservationEvent",
    "ObservationIdentity",
    "ReceiptIdentity",
    "RecoveryRequeueReason",
    "TransferDirection",
    "TransferIdentity",
    "TransferTerminalReason",
    "UINT32_MAX",
    "UINT64_MAX",
]
