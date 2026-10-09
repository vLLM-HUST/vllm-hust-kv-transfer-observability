# SPDX-License-Identifier: Apache-2.0
"""Bounded host-reported facts that are not cross-process recovery receipts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .schema import (
    MAX_RUNTIME_REQUEST_ID_BYTES,
    MAX_TRANSFER_ASSOCIATIONS,
    UINT32_MAX,
    UINT64_MAX,
    ComputeKind,
    RecoveryRequeueReason,
    _require_positive_uint,
    _require_printable_ascii,
    _require_uint,
)

HOST_FACT_SCHEMA = "vllm-hust.kv-transfer-host-fact.v1"
HOST_FACT_SCOPE = "host_reported_unjoined"
_PROCESS_UUID = re.compile(r"[0-9a-f]{32}")


class HostFactEvent(str, Enum):
    TRANSFER_RECEIPT = "transfer_receipt"
    RECOVERY_REQUEUED = "recovery_requeued"
    RECOVERY_ADMITTED = "recovery_admitted"
    FIRST_COMPUTE = "first_compute"


def _roster(value: object, name: str, maximum: int) -> None:
    if type(value) is not tuple or len(value) > MAX_TRANSFER_ASSOCIATIONS:
        raise ValueError(f"{name} must be a bounded tuple")
    for item in value:
        _require_uint(item, name, maximum)
    if tuple(sorted(set(value))) != value:
        raise ValueError(f"{name} must be sorted and unique")


@dataclass(frozen=True, slots=True)
class HostFact:
    """One process-local host claim, without a fabricated Worker association."""

    event: HostFactEvent
    process_uuid: str
    observed_at_ns: int
    request_id: str
    job_id: int | None = None
    rank: int | None = None
    recovery_epoch: int | None = None
    job_ids: tuple[int, ...] = ()
    ranks: tuple[int, ...] = ()
    compute_kind: ComputeKind | None = None
    requeue_reason: RecoveryRequeueReason | None = None

    def __post_init__(self) -> None:
        if type(self.event) is not HostFactEvent:
            raise ValueError("event must be a HostFactEvent")
        if type(self.process_uuid) is not str or not _PROCESS_UUID.fullmatch(
            self.process_uuid
        ):
            raise ValueError("process_uuid must be 32 lowercase hex digits")
        _require_uint(self.observed_at_ns, "observed_at_ns", UINT64_MAX)
        _require_printable_ascii(
            self.request_id, "request_id", MAX_RUNTIME_REQUEST_ID_BYTES
        )
        if self.job_id is not None:
            _require_uint(self.job_id, "job_id", UINT64_MAX)
        if self.rank is not None:
            _require_uint(self.rank, "rank", UINT32_MAX)
        if self.recovery_epoch is not None:
            _require_positive_uint(self.recovery_epoch, "recovery_epoch", UINT64_MAX)
        _roster(self.job_ids, "job_ids", UINT64_MAX)
        _roster(self.ranks, "ranks", UINT32_MAX)
        if self.compute_kind is not None and type(self.compute_kind) is not ComputeKind:
            raise ValueError("compute_kind must be a ComputeKind")
        if (
            self.requeue_reason is not None
            and type(self.requeue_reason) is not RecoveryRequeueReason
        ):
            raise ValueError("requeue_reason must be a RecoveryRequeueReason")
        shapes = {
            HostFactEvent.TRANSFER_RECEIPT: {"job_id", "ranks"},
            HostFactEvent.RECOVERY_REQUEUED: {"recovery_epoch", "requeue_reason"},
            HostFactEvent.RECOVERY_ADMITTED: {"recovery_epoch", "job_ids"},
            HostFactEvent.FIRST_COMPUTE: {
                "rank",
                "recovery_epoch",
                "job_ids",
                "compute_kind",
            },
        }
        populated = {
            name
            for name in (
                "job_id",
                "rank",
                "recovery_epoch",
                "job_ids",
                "ranks",
                "compute_kind",
                "requeue_reason",
            )
            if getattr(self, name) not in (None, ())
        }
        if populated != shapes[self.event]:
            raise ValueError("fields do not match the closed host fact schema")

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": HOST_FACT_SCHEMA,
            "scope": HOST_FACT_SCOPE,
            "event": self.event.value,
            "process_uuid": self.process_uuid,
            "observed_at_ns": self.observed_at_ns,
            "request_id": self.request_id,
        }
        for name in ("job_id", "rank", "recovery_epoch", "job_ids", "ranks"):
            value = getattr(self, name)
            if value not in (None, ()):
                payload[name] = list(value) if type(value) is tuple else value
        if self.compute_kind is not None:
            payload["compute_kind"] = self.compute_kind.value
        if self.requeue_reason is not None:
            payload["requeue_reason"] = self.requeue_reason.value
        return payload


__all__ = ["HOST_FACT_SCHEMA", "HOST_FACT_SCOPE", "HostFact", "HostFactEvent"]
