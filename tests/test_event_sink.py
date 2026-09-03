import json
import os
import threading
from pathlib import Path

import pytest

import vllm_hust_kv_transfer_observability.events as events_module
from vllm_hust_kv_transfer_observability import (
    ComputeKind,
    JsonlKVTransferEventSink,
    KVTransferObservation,
    ObservationEvent,
    ObservationIdentity,
    ReceiptIdentity,
    TransferDirection,
    TransferIdentity,
)

PROCESS_UUID = "b" * 32


def observation(
    sequence: int = 1,
    *,
    event: ObservationEvent = ObservationEvent.TRANSFER_COMPLETED,
) -> KVTransferObservation:
    identity = ObservationIdentity("request-1", 4, 0, recovery_epoch=2)
    transfer = TransferIdentity(f"{PROCESS_UUID}:t:{sequence}")
    if event is ObservationEvent.FIRST_COMPUTE:
        return KVTransferObservation(
            event,
            identity=identity,
            receipt=ReceiptIdentity(f"{PROCESS_UUID}:k:1"),
            associated_transfers=(transfer,),
            compute_kind=ComputeKind.PREFILL,
            observed_at_ns=20,
        )
    return KVTransferObservation(
        event,
        identity=identity,
        transfer=transfer,
        direction=TransferDirection.H2D,
        bytes_moved=128,
        duration_ns=9,
        observed_at_ns=20,
    )


def test_sink_is_explicit_default_off() -> None:
    sink = JsonlKVTransferEventSink(None)
    assert sink.emit(observation()) is False
    assert sink.close()
    assert sink.counters.enqueued == 0


def test_sink_rejects_invalid_wait_time_without_losing_shutdown(tmp_path: Path) -> None:
    sink = JsonlKVTransferEventSink(tmp_path / "events.jsonl")
    with pytest.raises(ValueError, match="non-negative"):
        sink.flush(float("nan"))
    with pytest.raises(ValueError, match="non-negative"):
        sink.close(-1)
    assert sink.close()


def test_sink_writes_only_the_closed_address_free_schema(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    with JsonlKVTransferEventSink(output) as sink:
        assert sink.emit(observation())
        assert sink.flush()
    payload = json.loads(output.read_text())
    assert payload["schema"] == "vllm-hust.kv-transfer-event.v2"
    assert payload["identity"] == {
        "rank": 0,
        "recovery_epoch": 2,
        "request_id": "request-1",
        "worker_generation": 4,
    }
    assert payload["transfer_id"] == f"{PROCESS_UUID}:t:1"
    assert "fields" not in payload
    assert "address" not in json.dumps(payload)
    assert "payload" not in json.dumps(payload)
    assert sink.counters.written == 1


def test_sink_rejects_untyped_call_without_raising(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    sink = JsonlKVTransferEventSink(output)
    assert sink.emit("event") is False  # type: ignore[arg-type]
    assert sink.close()
    assert sink.counters.serialization_errors == 1
    assert not output.exists()


def test_event_destination_symlinks_are_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "protected"
    target.write_text("protected")
    output = tmp_path / "events.jsonl"
    output.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        JsonlKVTransferEventSink(output)

    output.unlink()
    sink = JsonlKVTransferEventSink(output)
    output.symlink_to(target)
    assert sink.emit(observation())
    assert sink.flush()
    assert sink.close()
    assert target.read_text() == "protected"
    assert output.is_symlink()
    assert sink.counters.io_errors == 1


def test_record_and_file_bounds_are_counted(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    associations = tuple(
        sorted(
            TransferIdentity(f"{PROCESS_UUID}:t:{sequence}")
            for sequence in range(1, 40)
        )
    )
    oversized = KVTransferObservation(
        ObservationEvent.FIRST_COMPUTE,
        identity=ObservationIdentity("request", 0, 0, recovery_epoch=1),
        receipt=ReceiptIdentity(f"{PROCESS_UUID}:k:1"),
        associated_transfers=associations,
        compute_kind=ComputeKind.DECODE,
    )
    sink = JsonlKVTransferEventSink(
        output,
        max_record_bytes=512,
        max_file_bytes=1024,
    )
    assert sink.emit(oversized) is False
    for sequence in range(1, 10):
        assert sink.emit(observation(sequence))
    assert sink.flush()
    assert sink.close()
    assert sink.counters.record_size_dropped == 1
    assert sink.counters.file_capacity_dropped > 0
    assert output.stat().st_size <= 1024


def test_queue_overflow_is_nonblocking_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = JsonlKVTransferEventSink(
        tmp_path / "events.jsonl",
        max_pending_records=1,
    )
    started = threading.Event()
    release = threading.Event()

    def blocked_write(_: bytes) -> str:
        started.set()
        assert release.wait(2)
        return "written"

    monkeypatch.setattr(sink, "_write_record", blocked_write)
    assert sink.emit(observation(1))
    assert started.wait(2)
    assert sink.emit(observation(2))
    assert sink.emit(observation(3)) is False
    release.set()
    assert sink.close()
    assert sink.counters.queue_dropped == 1
    assert sink.counters.written == 2


def test_short_writes_are_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "events.jsonl"
    real_write = os.write
    calls = 0

    def short_write(descriptor: int, data) -> int:
        nonlocal calls
        calls += 1
        chunk = bytes(data[: max(1, len(data) // 2)])
        return real_write(descriptor, chunk)

    monkeypatch.setattr(events_module.os, "write", short_write)
    with JsonlKVTransferEventSink(output) as sink:
        assert sink.emit(observation())
    assert calls > 1
    assert json.loads(output.read_text())["event"] == "transfer_completed"


def test_partial_event_write_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "events.jsonl"
    real_write = os.write
    calls = 0

    def fail_after_partial(descriptor: int, data) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, bytes(data[:10]))
        raise OSError("disk full")

    monkeypatch.setattr(events_module.os, "write", fail_after_partial)
    sink = JsonlKVTransferEventSink(output)
    assert sink.emit(observation())
    assert sink.flush()
    assert sink.close()
    assert output.read_bytes() == b""
    assert sink.counters.io_errors == 1
    assert sink.counters.written == 0


def test_runtime_io_failure_is_fail_open_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = JsonlKVTransferEventSink(tmp_path / "events.jsonl")

    def failed_open() -> int:
        raise OSError("destination unavailable")

    monkeypatch.setattr(sink, "_open_descriptor", failed_open)
    assert sink.emit(observation())
    assert sink.flush()
    assert sink.close()
    assert sink.counters.io_errors == 1
    assert sink.counters.written == 0


def test_concurrent_emit_and_close_produce_only_complete_json(
    tmp_path: Path,
) -> None:
    output = tmp_path / "events.jsonl"
    sink = JsonlKVTransferEventSink(output, max_pending_records=256)
    failures: list[BaseException] = []

    def emit_many(offset: int) -> None:
        try:
            for sequence in range(offset, offset + 50):
                sink.emit(observation(sequence))
        except BaseException as exc:  # pragma: no cover - assertion carrier
            failures.append(exc)

    threads = [
        threading.Thread(target=emit_many, args=(1 + index * 50,)) for index in range(4)
    ]
    for thread in threads:
        thread.start()
    assert sink.close()
    for thread in threads:
        thread.join()
    assert not failures
    if output.exists():
        for line in output.read_text().splitlines():
            assert json.loads(line)["schema"].endswith(".v2")
    counters = sink.counters
    assert counters.written == counters.enqueued
    assert counters.enqueued + counters.closed_dropped == 200
