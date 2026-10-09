from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest

import vllm_hust_kv_transfer_observability.native as native
from vllm_hust_kv_transfer_observability.adapter import (
    HostObserverCallbacks,
    KVTransferHostAdapter,
)
from vllm_hust_kv_transfer_observability.config import ObserverConfig
from vllm_hust_kv_transfer_observability.host_facts import HostFact
from vllm_hust_kv_transfer_observability.native import (
    VllmOffloadingObserverBinding,
)
from vllm_hust_kv_transfer_observability.normalization import (
    CoreTransferCompleted,
    CoreTransferSubmitted,
)
from vllm_hust_kv_transfer_observability.validation import (
    read_events,
    validate_restore_chain,
)


class Value(str, Enum):
    SUBMITTED = "transfer_submitted"
    COMPLETED = "transfer_completed"
    RECOVERY = "recovery_admitted"
    REQUEUED = "recovery_requeued"
    RECEIPT = "transfer_receipt"
    FIRST_COMPUTE = "first_compute"
    DESCRIPTORS = "transfer_descriptors"
    H2D = "h2d_restore"
    D2H = "d2h_preserve"
    CANCELLED = "transfer_cancelled"
    HOST_SHUTDOWN = "host_shutdown"
    UNCLASSIFIED = "unclassified"
    DECODE = "decode"


class FakeHost:
    KV_TRANSFER_OBSERVER_CONTRACT = "vllm.kv-transfer.observer.v1"
    KV_TRANSFER_OBSERVABILITY_API_VERSION = "1.0"

    def __init__(self) -> None:
        self.callback = None
        self.handle = object()
        self.unregistered = None

    def register_kv_transfer_observer(self, name, callback):
        assert name == "kv_transfer_observability"
        self.callback = callback
        return self.handle

    def unregister_kv_transfer_observer(self, handle):
        self.unregistered = handle


def record(event: Value, **changes):
    values = {
        "event": event,
        "operation": Value.H2D,
        "job_id": 7,
        "rank": 1,
        "request_id": "request-7",
        "block_count": 2,
        "success": None,
        "bytes_moved": None,
        "duration_ns": None,
        "reason": None,
        "observed_at_ns": 10,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_binding_translates_worker_submit_and_completion() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    seen = []

    def observe(item):
        seen.append(item)
        return True

    handle = binding.register(HostObserverCallbacks(observe, None))
    assert host.callback(record(Value.SUBMITTED)) is True
    assert (
        host.callback(
            record(
                Value.COMPLETED,
                success=True,
                bytes_moved=4096,
                duration_ns=4,
                observed_at_ns=20,
            )
        )
        is True
    )

    assert isinstance(seen[0], CoreTransferSubmitted)
    assert isinstance(seen[1], CoreTransferCompleted)
    assert seen[0].transfer == seen[1].transfer
    assert seen[0].identity.worker_generation > 0
    assert seen[1].receipt is not None
    binding.unregister(handle)
    assert host.unregistered is host.handle
    assert host.callback(record(Value.SUBMITTED)) is False


def test_rejected_submit_never_creates_a_pending_transfer() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    seen = []

    def observe(item):
        seen.append(item)
        return len(seen) != 1

    binding.register(HostObserverCallbacks(observe, None))
    assert host.callback(record(Value.SUBMITTED)) is False
    assert host.callback(record(Value.COMPLETED, success=True, bytes_moved=1)) is False
    assert len(seen) == 1
    assert host.callback(record(Value.SUBMITTED)) is True
    assert host.callback(record(Value.COMPLETED, success=True, bytes_moved=1)) is True


def test_binding_bounds_pending_transfers_and_recovers_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "MAX_BINDING_TRANSFERS", 1)
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    seen = []

    def observe(item):
        seen.append(item)
        return True

    binding.register(HostObserverCallbacks(observe, None))
    assert host.callback(record(Value.SUBMITTED)) is True
    assert host.callback(record(Value.SUBMITTED, job_id=8)) is False
    assert len(seen) == 1
    assert host.callback(record(Value.COMPLETED, success=False)) is True
    assert host.callback(record(Value.SUBMITTED, job_id=8)) is True


def test_duplicate_and_mismatched_terminal_do_not_replace_original() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    seen = []

    def observe(item):
        seen.append(item)
        return True

    binding.register(HostObserverCallbacks(observe, None))
    assert host.callback(record(Value.SUBMITTED)) is True
    assert host.callback(record(Value.SUBMITTED, request_id="other")) is False
    assert (
        host.callback(record(Value.COMPLETED, request_id="other", success=True))
        is False
    )
    assert host.callback(record(Value.COMPLETED, rank=2, success=True)) is False
    assert (
        host.callback(record(Value.COMPLETED, operation=Value.D2H, success=True))
        is False
    )
    assert len(seen) == 1
    assert host.callback(record(Value.COMPLETED, success=True)) is True
    assert len(seen) == 2


def test_malformed_callback_is_fail_open_then_accepts_valid_terminal() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    seen = []

    def observe(item):
        seen.append(item)
        return True

    binding.register(HostObserverCallbacks(observe, None))
    invalid_event = record(Value.SUBMITTED)
    invalid_event.event = SimpleNamespace(value=[])
    assert host.callback(invalid_event) is False
    for invalid in (
        record(Value.SUBMITTED, job_id=True),
        record(Value.SUBMITTED, rank=True),
        record(Value.SUBMITTED, request_id="\x00"),
        record(Value.SUBMITTED, block_count=0),
    ):
        assert host.callback(invalid) is False
    assert host.callback(record(Value.SUBMITTED)) is True
    assert host.callback(record(Value.COMPLETED, success="yes")) is False
    assert host.callback(record(Value.COMPLETED, success=True)) is True
    assert len(seen) == 2


def test_worker_incarnations_use_distinct_local_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(("1" * 32, "2" * 32))
    monkeypatch.setattr(native.secrets, "token_hex", lambda _n: next(tokens))
    hosts = (FakeHost(), FakeHost())
    seen = ([], [])
    for host, items in zip(hosts, seen, strict=True):
        binding = VllmOffloadingObserverBinding(host)

        def observe(item, destination=items):
            destination.append(item)
            return True

        binding.register(HostObserverCallbacks(observe, None))
        assert host.callback(record(Value.SUBMITTED)) is True
    assert (
        seen[0][0].identity.worker_generation != seen[1][0].identity.worker_generation
    )
    assert seen[0][0].transfer != seen[1][0].transfer


def test_worker_only_binding_continues_after_recovery_table_limit(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    path = tmp_path / "events.jsonl"
    adapter = KVTransferHostAdapter(
        ObserverConfig(
            enabled=True,
            event_path=path,
            max_correlated_transfers=1,
        ),
        retain_restore_receipts=False,
    )
    assert adapter.start(VllmOffloadingObserverBinding(host))
    for job_id in (7, 8, 9):
        assert host.callback(record(Value.SUBMITTED, job_id=job_id)) is True
        assert (
            host.callback(
                record(
                    Value.COMPLETED,
                    job_id=job_id,
                    success=True,
                    bytes_moved=4096,
                    observed_at_ns=20,
                )
            )
            is True
        )
    assert adapter.stop()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "restore_started",
        "restore_completed",
    ] * 3


def test_binding_rejects_cross_process_recovery_without_inventing_identity() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    binding.register(HostObserverCallbacks(lambda _item: True, None))

    assert host.callback(record(Value.RECOVERY, job_id=None, rank=None)) is False
    assert host.callback(record(Value.FIRST_COMPUTE, job_id=None)) is False
    assert host.callback(record(Value.DESCRIPTORS, rank=None, request_id=None)) is False


def test_host_v1_recovery_facts_are_recorded_without_false_chain(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    path = tmp_path / "events.jsonl"
    adapter = KVTransferHostAdapter(
        ObserverConfig(enabled=True, event_path=path),
        retain_restore_receipts=False,
    )
    assert adapter.start(VllmOffloadingObserverBinding(host))
    assert host.callback(
        record(
            Value.REQUEUED,
            job_id=None,
            rank=None,
            recovery_epoch=1,
            requeue_reason=Value.UNCLASSIFIED,
        )
    )
    assert host.callback(record(Value.RECEIPT, rank=None, ranks=(0, 1), success=True))
    assert host.callback(
        record(Value.RECOVERY, job_id=None, rank=None, recovery_epoch=1, job_ids=(7,))
    )
    assert host.callback(
        record(
            Value.FIRST_COMPUTE,
            job_id=None,
            rank=0,
            recovery_epoch=1,
            job_ids=(7,),
            compute_kind=Value.DECODE,
        )
    )
    assert adapter.stop()
    facts = read_events(path)
    assert len(facts) == 4
    assert all(type(fact) is HostFact for fact in facts)
    assert [fact.event.value for fact in facts] == [
        "recovery_requeued",
        "transfer_receipt",
        "recovery_admitted",
        "first_compute",
    ]
    assert len({fact.process_uuid for fact in facts}) == 1
    assert adapter.counters.host_facts_emitted == 4
    with pytest.raises(ValueError, match="absent or incomplete"):
        validate_restore_chain(facts, "request-7")


def test_host_fact_rejects_malformed_rosters_and_missing_identity(
    tmp_path: Path,
) -> None:
    host = FakeHost()
    path = tmp_path / "events.jsonl"
    adapter = KVTransferHostAdapter(ObserverConfig(enabled=True, event_path=path))
    assert adapter.start(VllmOffloadingObserverBinding(host))
    for invalid in (
        record(Value.RECEIPT, rank=None, ranks=(1, 1)),
        record(Value.RECEIPT, rank=None, ranks=(1, 0)),
        record(Value.RECEIPT, rank=None, ranks=()),
        record(Value.RECEIPT, rank=None, ranks=(True,)),
        record(Value.RECEIPT, rank=None, ranks=(0,), success=False),
        record(Value.RECEIPT, rank=None, ranks=(0,), operation=Value.D2H),
        record(Value.RECOVERY, job_id=None, rank=None, recovery_epoch=0, job_ids=(7,)),
        record(
            Value.RECOVERY,
            job_id=None,
            rank=None,
            recovery_epoch=1,
            job_ids=(7, 7),
        ),
        record(
            Value.FIRST_COMPUTE,
            job_id=None,
            rank=None,
            recovery_epoch=1,
            job_ids=(7,),
            compute_kind=Value.DECODE,
        ),
        record(
            Value.REQUEUED,
            job_id=None,
            rank=None,
            recovery_epoch=1,
            requeue_reason=Value.UNCLASSIFIED,
            request_id="\x00",
        ),
    ):
        assert host.callback(invalid) is False
    assert host.callback(
        record(
            Value.REQUEUED,
            job_id=None,
            rank=None,
            recovery_epoch=1,
            requeue_reason=Value.UNCLASSIFIED,
        )
    )
    assert adapter.stop()
    assert len(read_events(path)) == 1


def test_reused_job_id_in_different_processes_does_not_claim_shared_identity(
    tmp_path: Path,
) -> None:
    facts = []
    for index in (0, 1):
        host = FakeHost()
        path = tmp_path / f"events-{index}.jsonl"
        adapter = KVTransferHostAdapter(ObserverConfig(enabled=True, event_path=path))
        assert adapter.start(VllmOffloadingObserverBinding(host))
        assert host.callback(record(Value.RECEIPT, rank=None, ranks=(0,), success=True))
        assert adapter.stop()
        facts.extend(read_events(path))
    assert len(facts) == 2
    assert facts[0].job_id == facts[1].job_id == 7
    assert facts[0].process_uuid != facts[1].process_uuid
    with pytest.raises(ValueError, match="absent or incomplete"):
        validate_restore_chain(facts, "request-7")


def test_reused_rank_and_job_after_worker_restart_cannot_join_scheduler_fact(
    tmp_path: Path,
) -> None:
    workers = []
    for index in (0, 1):
        host = FakeHost()
        path = tmp_path / f"worker-{index}.jsonl"
        adapter = KVTransferHostAdapter(
            ObserverConfig(enabled=True, event_path=path),
            retain_restore_receipts=False,
        )
        assert adapter.start(VllmOffloadingObserverBinding(host))
        assert host.callback(record(Value.SUBMITTED, rank=0))
        assert host.callback(
            record(
                Value.COMPLETED,
                rank=0,
                success=True,
                bytes_moved=4096,
                observed_at_ns=20,
            )
        )
        assert adapter.stop()
        workers.extend(read_events(path))
    scheduler = FakeHost()
    scheduler_path = tmp_path / "scheduler.jsonl"
    adapter = KVTransferHostAdapter(
        ObserverConfig(enabled=True, event_path=scheduler_path)
    )
    assert adapter.start(VllmOffloadingObserverBinding(scheduler))
    assert scheduler.callback(
        record(Value.RECEIPT, rank=None, ranks=(0,), success=True)
    )
    assert scheduler.callback(
        record(Value.RECOVERY, rank=None, job_id=None, recovery_epoch=1, job_ids=(7,))
    )
    assert adapter.stop()
    facts = read_events(scheduler_path)
    assert workers[0].identity.rank == workers[2].identity.rank == facts[0].ranks[0]
    assert workers[0].transfer != workers[2].transfer
    assert facts[0].process_uuid not in {
        workers[0].transfer.process_uuid,
        workers[2].transfer.process_uuid,
    }
    with pytest.raises(ValueError):
        validate_restore_chain(workers + facts, "request-7")


def test_binding_fails_closed_on_unknown_host_api() -> None:
    host = FakeHost()
    host.KV_TRANSFER_OBSERVABILITY_API_VERSION = "2.0"

    try:
        VllmOffloadingObserverBinding(host)
    except RuntimeError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unknown host API must be rejected")
