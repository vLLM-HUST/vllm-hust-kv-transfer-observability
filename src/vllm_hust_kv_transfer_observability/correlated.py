# SPDX-License-Identifier: Apache-2.0
"""Closed, address-free records for the host's cross-process recovery seam."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

CORRELATED_SCHEMA = "vllm-hust.kv-transfer-correlated-host.v2"
MAX_CORRELATED_ROSTER = 4096
_UUID = re.compile(r"[0-9a-f]{32}")
_UINT64_MAX = 2**64 - 1
_UINT32_MAX = 2**32 - 1


def _uuid(value: object) -> None:
    if type(value) is not str or not _UUID.fullmatch(value):
        raise ValueError("generation must be 32 lowercase hex digits")


def _uint(value: object, maximum: int) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("identity must be an unsigned integer")


@dataclass(frozen=True, slots=True, order=True)
class WorkerReceipt:
    rank: int
    worker_generation: str

    def __post_init__(self) -> None:
        _uint(self.rank, _UINT32_MAX)
        _uuid(self.worker_generation)

    def to_payload(self) -> dict[str, object]:
        return {"rank": self.rank, "worker_generation": self.worker_generation}


@dataclass(frozen=True, slots=True, order=True)
class JobReceipt:
    job_id: int
    workers: tuple[WorkerReceipt, ...]

    def __post_init__(self) -> None:
        _uint(self.job_id, _UINT64_MAX)
        if (
            type(self.workers) is not tuple
            or not 0 < len(self.workers) <= MAX_CORRELATED_ROSTER
            or any(type(worker) is not WorkerReceipt for worker in self.workers)
            or tuple(sorted(set(self.workers))) != self.workers
            or len({worker.rank for worker in self.workers}) != len(self.workers)
        ):
            raise ValueError("workers must be an exact sorted rank roster")

    def to_payload(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "workers": [worker.to_payload() for worker in self.workers],
        }


class CorrelatedEvent(str, Enum):
    RECOVERY_REQUEUED = "recovery_requeued"
    RESTORE_SUBMITTED = "restore_submitted"
    RESTORE_COMPLETED = "restore_completed"
    TRANSFER_RECEIPT = "transfer_receipt"
    RECOVERY_ADMITTED = "recovery_admitted"
    FIRST_COMPUTE = "first_compute"


@dataclass(frozen=True, slots=True)
class CorrelatedHostObservation:
    """One host-provided link; a complete chain is established only offline."""

    event: CorrelatedEvent
    scheduler_generation: str
    request_id: str
    recovery_epoch: int
    observed_at_ns: int
    job_id: int | None = None
    rank: int | None = None
    worker_generation: str | None = None
    block_count: int | None = None
    workers: tuple[WorkerReceipt, ...] = ()
    roster: tuple[JobReceipt, ...] = ()
    compute_kind: str | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not CorrelatedEvent:
            raise ValueError("event must be a CorrelatedEvent")
        _uuid(self.scheduler_generation)
        if (
            type(self.request_id) is not str
            or not self.request_id.isascii()
            or not self.request_id.isprintable()
            or not 0 < len(self.request_id) <= 128
        ):
            raise ValueError("request_id must be bounded printable ASCII")
        _uint(self.recovery_epoch, _UINT64_MAX)
        if self.recovery_epoch == 0:
            raise ValueError("recovery_epoch must be positive")
        _uint(self.observed_at_ns, _UINT64_MAX)
        for value, maximum in (
            (self.job_id, _UINT64_MAX),
            (self.rank, _UINT32_MAX),
            (self.block_count, _UINT32_MAX),
        ):
            if value is not None:
                _uint(value, maximum)
        if self.block_count == 0:
            raise ValueError("block_count must be positive")
        if self.worker_generation is not None:
            _uuid(self.worker_generation)
        if (
            type(self.workers) is not tuple
            or len(self.workers) > MAX_CORRELATED_ROSTER
            or any(type(item) is not WorkerReceipt for item in self.workers)
            or tuple(sorted(set(self.workers))) != self.workers
            or len({item.rank for item in self.workers}) != len(self.workers)
        ):
            raise ValueError("workers must be a bounded exact roster")
        if (
            type(self.roster) is not tuple
            or len(self.roster) > MAX_CORRELATED_ROSTER
            or any(type(item) is not JobReceipt for item in self.roster)
            or tuple(sorted(set(self.roster))) != self.roster
            or len({item.job_id for item in self.roster}) != len(self.roster)
            or sum(len(item.workers) for item in self.roster) > MAX_CORRELATED_ROSTER
        ):
            raise ValueError("roster must be a bounded exact job roster")
        required = {
            CorrelatedEvent.RECOVERY_REQUEUED: set(),
            CorrelatedEvent.RESTORE_SUBMITTED: {
                "job_id",
                "rank",
                "worker_generation",
                "block_count",
            },
            CorrelatedEvent.RESTORE_COMPLETED: {"job_id", "rank", "worker_generation"},
            CorrelatedEvent.TRANSFER_RECEIPT: {"job_id", "workers"},
            CorrelatedEvent.RECOVERY_ADMITTED: {"roster"},
            CorrelatedEvent.FIRST_COMPUTE: {
                "rank",
                "worker_generation",
                "roster",
                "compute_kind",
            },
        }
        fields = {
            name
            for name in (
                "job_id",
                "rank",
                "worker_generation",
                "block_count",
                "workers",
                "roster",
                "compute_kind",
            )
            if getattr(self, name) not in (None, ())
        }
        if fields != required[self.event]:
            raise ValueError("fields do not match the correlated event shape")
        if self.compute_kind is not None and self.compute_kind not in {
            "prefill",
            "decode",
        }:
            raise ValueError("compute_kind must be prefill or decode")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": CORRELATED_SCHEMA,
            "event": self.event.value,
            "scheduler_generation": self.scheduler_generation,
            "request_id": self.request_id,
            "recovery_epoch": self.recovery_epoch,
            "observed_at_ns": self.observed_at_ns,
        }
        for name in (
            "job_id",
            "rank",
            "worker_generation",
            "block_count",
            "compute_kind",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.workers:
            payload["workers"] = [worker.to_payload() for worker in self.workers]
        if self.roster:
            payload["roster"] = [job.to_payload() for job in self.roster]
        return payload


__all__ = [
    "CORRELATED_SCHEMA",
    "CorrelatedEvent",
    "CorrelatedHostObservation",
    "JobReceipt",
    "WorkerReceipt",
]
