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
from vllm_hust_kv_transfer_observability.native import (
    VllmOffloadingObserverBinding,
)
from vllm_hust_kv_transfer_observability.normalization import (
    CoreTransferCompleted,
    CoreTransferSubmitted,
)


class Value(str, Enum):
    SUBMITTED = "transfer_submitted"
    COMPLETED = "transfer_completed"
    RECOVERY = "recovery_admitted"
    FIRST_COMPUTE = "first_compute"
    DESCRIPTORS = "transfer_descriptors"
    H2D = "h2d_restore"
    D2H = "d2h_preserve"
    CANCELLED = "transfer_cancelled"
    HOST_SHUTDOWN = "host_shutdown"


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


def test_binding_fails_closed_on_unknown_host_api() -> None:
    host = FakeHost()
    host.KV_TRANSFER_OBSERVABILITY_API_VERSION = "2.0"

    try:
        VllmOffloadingObserverBinding(host)
    except RuntimeError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unknown host API must be rejected")
