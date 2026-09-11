# SPDX-License-Identifier: Apache-2.0
"""Closed semantic inventory for legacy B134 host observations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from .schema import ObservationEvent


class B134Event(str, Enum):
    """The 14 event names emitted by merged legacy core PR #220."""

    PREEMPT = "preempt"
    WAKEUP = "wakeup"
    ADMISSION = "admission"
    SCHEDULED = "scheduled"
    RESTORE_START = "restore_start"
    RESTORE_DONE = "restore_done"
    CPU_STORE = "cpu_store"
    CPU_EVICT = "cpu_evict"
    EVICT = "evict"
    SWAP_D2H_SUBMIT = "swap_d2h_submit"
    GATHER_H2D = "gather_h2d"
    TRANSFER_SUBMIT = "transfer_submit"
    COPY_OBSERVED_COMPLETE = "copy_observed_complete"
    SCHED_STEP = "sched_step"


class B134Owner(str, Enum):
    SCHEDULER = "scheduler"
    KV_OFFLOAD = "kv_offload"


class B134IdentityKind(str, Enum):
    REQUEST = "request"
    TRANSFER = "transfer"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class B134EventContract:
    """Source ownership, fields, and optional semantic correspondence."""

    owner: B134Owner
    identity_kind: B134IdentityKind
    fields: tuple[str, ...]
    canonical_event: ObservationEvent | None = None


_CONTRACTS = {
    B134Event.PREEMPT: B134EventContract(
        B134Owner.SCHEDULER, B134IdentityKind.REQUEST, ()
    ),
    B134Event.WAKEUP: B134EventContract(
        B134Owner.SCHEDULER, B134IdentityKind.REQUEST, ()
    ),
    B134Event.ADMISSION: B134EventContract(
        B134Owner.SCHEDULER, B134IdentityKind.REQUEST, ()
    ),
    B134Event.SCHEDULED: B134EventContract(
        B134Owner.SCHEDULER, B134IdentityKind.REQUEST, ()
    ),
    B134Event.RESTORE_START: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.REQUEST,
        ("keys",),
        ObservationEvent.RESTORE_STARTED,
    ),
    B134Event.RESTORE_DONE: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.REQUEST,
        ("keys",),
        ObservationEvent.RESTORE_COMPLETED,
    ),
    B134Event.CPU_STORE: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.REQUEST,
        ("duration_us", "evicted_keys", "stored_keys"),
    ),
    B134Event.CPU_EVICT: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.SYSTEM,
        ("duration_us", "evicted_keys"),
    ),
    B134Event.EVICT: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.REQUEST,
        ("duration_us", "keys"),
    ),
    B134Event.SWAP_D2H_SUBMIT: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.TRANSFER,
        ("descriptors", "duration_us"),
    ),
    B134Event.GATHER_H2D: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.TRANSFER,
        ("dma_runs", "duration_us"),
    ),
    B134Event.TRANSFER_SUBMIT: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.TRANSFER,
        (
            "bytes",
            "dependency_us",
            "descriptor_us",
            "descriptors",
            "direction",
            "submit_us",
        ),
        ObservationEvent.TRANSFER_STARTED,
    ),
    B134Event.COPY_OBSERVED_COMPLETE: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.TRANSFER,
        ("bytes", "completion_observed_ms", "device_event_ms", "direction"),
        ObservationEvent.TRANSFER_COMPLETED,
    ),
    B134Event.SCHED_STEP: B134EventContract(
        B134Owner.KV_OFFLOAD,
        B134IdentityKind.SYSTEM,
        ("duration_us",),
    ),
}

B134_EVENT_CONTRACTS: Final[Mapping[B134Event, B134EventContract]] = MappingProxyType(
    _CONTRACTS
)
B134_EVENT_COUNT: Final = 14
UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE: Final = 15
B134_RECOVERY_CHAIN: Final = (
    B134Event.PREEMPT,
    B134Event.RESTORE_START,
    B134Event.RESTORE_DONE,
    B134Event.WAKEUP,
    B134Event.ADMISSION,
    B134Event.SCHEDULED,
)


def b134_event_contract(event: B134Event) -> B134EventContract:
    """Return the fixed contract for a typed B134 event."""
    if type(event) is not B134Event:
        raise ValueError("event must be a B134Event")
    return B134_EVENT_CONTRACTS[event]


__all__ = [
    "B134_EVENT_CONTRACTS",
    "B134_EVENT_COUNT",
    "B134_RECOVERY_CHAIN",
    "UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE",
    "B134Event",
    "B134EventContract",
    "B134IdentityKind",
    "B134Owner",
    "b134_event_contract",
]
