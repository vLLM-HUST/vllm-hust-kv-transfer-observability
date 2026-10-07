from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

from vllm_hust_kv_transfer_observability.adapter import HostObserverCallbacks
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
    H2D = "h2d_restore"


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
    assert seen[1].receipt is not None
    binding.unregister(handle)
    assert host.unregistered is host.handle


def test_binding_rejects_cross_process_recovery_without_inventing_identity() -> None:
    host = FakeHost()
    binding = VllmOffloadingObserverBinding(host)
    binding.register(HostObserverCallbacks(lambda _item: True, None))

    assert host.callback(record(Value.RECOVERY, job_id=None, rank=None)) is False


def test_binding_fails_closed_on_unknown_host_api() -> None:
    host = FakeHost()
    host.KV_TRANSFER_OBSERVABILITY_API_VERSION = "2.0"

    try:
        VllmOffloadingObserverBinding(host)
    except RuntimeError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unknown host API must be rejected")
