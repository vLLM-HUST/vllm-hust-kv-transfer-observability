"""Cross-process recovery proofs use host-carried identities, not clocks."""

from __future__ import annotations

import json
from dataclasses import replace
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

from vllm_hust_kv_transfer_observability.adapter import (
    HostObserverCallbacks,
    KVTransferHostAdapter,
)
from vllm_hust_kv_transfer_observability.config import ObserverConfig
from vllm_hust_kv_transfer_observability.correlated import (
    CorrelatedEvent,
    CorrelatedHostObservation,
    JobReceipt,
    WorkerReceipt,
)
from vllm_hust_kv_transfer_observability.native import VllmOffloadingObserverBinding
from vllm_hust_kv_transfer_observability.validation import (
    correlated_from_payload,
    main,
    read_events,
    validate_restore_chain,
)

SCHEDULER = "a" * 32
WORKER_0 = "b" * 32
WORKER_1 = "c" * 32
ROSTER = (JobReceipt(7, (WorkerReceipt(0, WORKER_0), WorkerReceipt(1, WORKER_1))),)


def chain() -> list[CorrelatedHostObservation]:
    common = dict(scheduler_generation=SCHEDULER, request_id="req", recovery_epoch=1)
    return [
        CorrelatedHostObservation(
            CorrelatedEvent.RECOVERY_REQUEUED, **common, observed_at_ns=1
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.RESTORE_SUBMITTED,
            **common,
            observed_at_ns=2,
            job_id=7,
            rank=0,
            worker_generation=WORKER_0,
            block_count=2,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.RESTORE_COMPLETED,
            **common,
            observed_at_ns=3,
            job_id=7,
            rank=0,
            worker_generation=WORKER_0,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.RESTORE_SUBMITTED,
            **common,
            observed_at_ns=200,
            job_id=7,
            rank=1,
            worker_generation=WORKER_1,
            block_count=2,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.RESTORE_COMPLETED,
            **common,
            observed_at_ns=300,
            job_id=7,
            rank=1,
            worker_generation=WORKER_1,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.TRANSFER_RECEIPT,
            **common,
            observed_at_ns=5,
            job_id=7,
            workers=ROSTER[0].workers,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.RECOVERY_ADMITTED,
            **common,
            observed_at_ns=6,
            roster=ROSTER,
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.FIRST_COMPUTE,
            **common,
            observed_at_ns=4,
            rank=0,
            worker_generation=WORKER_0,
            roster=ROSTER,
            compute_kind="decode",
        ),
        CorrelatedHostObservation(
            CorrelatedEvent.FIRST_COMPUTE,
            **common,
            observed_at_ns=400,
            rank=1,
            worker_generation=WORKER_1,
            roster=ROSTER,
            compute_kind="decode",
        ),
    ]


def test_cross_process_records_roundtrip_and_validate_without_shared_clock(
    tmp_path: Path,
) -> None:
    records = chain()
    assert [
        correlated_from_payload(record.to_payload()) for record in records
    ] == records
    # Worker 1's monotonic clock is far ahead of the scheduler's clock.
    assert validate_restore_chain(records, "req") == 1
    scheduler_path, worker_path = (
        tmp_path / "scheduler.jsonl",
        tmp_path / "worker.jsonl",
    )
    for path, items in (
        (scheduler_path, [records[0], records[5], records[6]]),
        (worker_path, records[1:5] + records[7:]),
    ):
        path.write_text("".join(json.dumps(item.to_payload()) + "\n" for item in items))
    # Repeatable multi-process ingestion accepts multiple JSONL artifacts.
    assert (
        main(
            [
                "--events",
                str(scheduler_path),
                "--events",
                str(worker_path),
                "--expect-restore-chain",
                "req",
            ]
        )
        == 0
    )


@pytest.mark.parametrize("index", range(9))
def test_missing_correlated_stage_is_not_proven(index: int) -> None:
    records = chain()
    del records[index]
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req")


@pytest.mark.parametrize("index", range(9))
def test_duplicate_correlated_stage_is_not_proven(index: int) -> None:
    records = chain()
    records.append(records[index])
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req")


def test_restarted_worker_cannot_borrow_old_generation_receipt() -> None:
    records = chain()
    records[4] = replace(records[4], worker_generation="d" * 32)
    with pytest.raises(ValueError, match="roster"):
        validate_restore_chain(records, "req")


def test_epoch_and_job_reuse_are_rejected() -> None:
    records = chain()
    records[2] = replace(records[2], recovery_epoch=2)
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req")
    records = chain()
    next_epoch = [replace(record, recovery_epoch=2) for record in records]
    with pytest.raises(ValueError, match="reused"):
        validate_restore_chain(records + next_epoch, "req")


def test_malformed_nested_roster_is_rejected() -> None:
    payload = chain()[6].to_payload()
    payload["roster"][0]["workers"][0]["worker_generation"] = "old"
    with pytest.raises(ValueError):
        correlated_from_payload(payload)


class FakeV1:
    KV_TRANSFER_OBSERVER_CONTRACT = "vllm.kv-transfer.observer.v1"
    KV_TRANSFER_OBSERVABILITY_API_VERSION = "1.0"

    def __init__(self) -> None:
        self.callback = None
        self.unregistered = False

    def register_kv_transfer_observer(self, _name, callback):
        self.callback = callback
        return object()

    def unregister_kv_transfer_observer(self, _handle):
        self.unregistered = True


class FakeV2:
    KV_TRANSFER_CORRELATED_OBSERVER_CONTRACT = "vllm.kv-transfer.observer.v2"
    KV_TRANSFER_CORRELATED_OBSERVABILITY_API_VERSION = "2.0"

    def __init__(self) -> None:
        self.callback = None
        self.unregistered = False

    def register_correlated_observer(self, callback):
        self.callback = callback
        return object()

    def unregister_correlated_observer(self, _handle):
        self.unregistered = True


class HostEvent(str, Enum):
    RECOVERY_REQUEUED = "recovery_requeued"


def test_binding_consumes_v2_host_callback_and_unregisters_both(tmp_path: Path) -> None:
    v1, v2 = FakeV1(), FakeV2()
    adapter = KVTransferHostAdapter(
        ObserverConfig(enabled=True, event_path=tmp_path / "events.jsonl")
    )
    assert adapter.start(VllmOffloadingObserverBinding(v1, v2))
    source = chain()[0]
    raw = SimpleNamespace(
        **{field: getattr(source, field) for field in source.__dataclass_fields__}
    )
    raw.event = HostEvent.RECOVERY_REQUEUED
    assert v2.callback(raw) is True
    raw.scheduler_generation = "invalid"
    assert v2.callback(raw) is False
    assert adapter.stop()
    assert v1.unregistered and v2.unregistered
    assert read_events(tmp_path / "events.jsonl") == [source]
    assert adapter.counters.correlated_emitted == 1


def test_failed_v2_registration_rolls_back_v1() -> None:
    class BrokenV2(FakeV2):
        def register_correlated_observer(self, callback):
            raise RuntimeError("registration failed")

    v1 = FakeV1()
    with pytest.raises(RuntimeError, match="registration failed"):
        VllmOffloadingObserverBinding(v1, BrokenV2()).register(
            HostObserverCallbacks(
                lambda _item: True, None, correlated=lambda _item: True
            )
        )
    assert v1.unregistered
