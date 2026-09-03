"""Contract tests migrated from the legacy B134 suite (core-pr-220).

These tests pin the sink/descriptor semantics that legacy PR #220 defined
(append ordering, fail-closed descriptors, default-off zero side effects)
without importing any vLLM runtime module, so they run in plain pytest.
"""

import json
import os
import threading

import pytest

from vllm_hust_kv_transfer_observability import (
    ALLOWED_EVIDENCE_LABELS,
    DescriptorLayoutCapture,
    JsonlKVTransferEventSink,
)

EVENT_SCHEMA = "vllm-hust.kv-transfer-event.v1"
DESCRIPTOR_SCHEMA = "vllm-hust.kv-transfer-descriptor-layout.v1"


# --- JsonlKVTransferEventSink ---


def test_sink_default_off_has_no_side_effects() -> None:
    sink = JsonlKVTransferEventSink(None)
    assert sink.enabled is False
    # A disabled sink must not raise and must not create any file.
    sink.emit("preempt", "request-1")
    sink.emit("restore_start", "request-1", keys=3)
    sink.close()


def test_sink_is_explicitly_enabled_by_path(tmp_path) -> None:
    sink = JsonlKVTransferEventSink(tmp_path / "events.jsonl")
    assert sink.enabled is True
    sink.close()


def test_sink_appends_preserve_event_order(tmp_path) -> None:
    """JSONL must keep the exact per-request emit order (legacy contract)."""
    output = tmp_path / "chain.jsonl"
    with JsonlKVTransferEventSink(output) as sink:
        for event in ("wakeup", "admission", "scheduled"):
            sink.emit(event, "request-1")

    events = [json.loads(line)["event"] for line in output.read_text().splitlines()]
    assert events == ["wakeup", "admission", "scheduled"]


def test_sink_rejects_empty_event_or_request_id(tmp_path) -> None:
    sink = JsonlKVTransferEventSink(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        sink.emit("", "request-1")
    with pytest.raises(ValueError):
        sink.emit("preempt", "")
    sink.close()


def test_sink_records_schema_pid_and_timestamp(tmp_path) -> None:
    output = tmp_path / "events.jsonl"
    with JsonlKVTransferEventSink(output) as sink:
        sink.emit("transfer_submit", "job1", bytes=4096, descriptors=4)

    payload = json.loads(output.read_text())
    assert payload["schema"] == EVENT_SCHEMA
    assert payload["event"] == "transfer_submit"
    assert payload["request_id"] == "job1"
    assert payload["fields"] == {"bytes": 4096, "descriptors": 4}
    assert isinstance(payload["pid"], int)
    assert isinstance(payload["ts_monotonic_ns"], int)


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX O_APPEND guarantees atomic writes; Windows emulates append",
)
def test_sink_append_is_thread_safe(tmp_path) -> None:
    output = tmp_path / "events.jsonl"
    sink = JsonlKVTransferEventSink(output)
    threads = 8
    per_thread = 25
    barrier = threading.Barrier(threads)

    def worker(worker_id: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            sink.emit("cpu_store", f"req-{worker_id}-{i}", stored_keys=1)

    runners = [
        threading.Thread(target=worker, args=(worker_id,))
        for worker_id in range(threads)
    ]
    for runner in runners:
        runner.start()
    for runner in runners:
        runner.join()
    sink.close()

    lines = output.read_text().splitlines()
    assert len(lines) == threads * per_thread
    for line in lines:
        assert json.loads(line)["schema"] == EVENT_SCHEMA


# --- DescriptorLayoutCapture ---


def _descriptor(**overrides):
    descriptor = {
        "src_offset": 0,
        "dst_offset": 8,
        "size": 16,
        "direction": "h2d",
    }
    descriptor.update(overrides)
    return descriptor


def test_capture_requires_existing_directory(tmp_path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        DescriptorLayoutCapture(tmp_path / "missing", "real-online")


def test_capture_rejects_unknown_evidence_label(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported evidence label"):
        DescriptorLayoutCapture(tmp_path, "not-a-label")
    assert "real-online" in ALLOWED_EVIDENCE_LABELS


def test_capture_is_fail_closed_on_schema(tmp_path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    with pytest.raises(ValueError, match="fields"):
        capture.capture(job_id=1, direction="h2d", descriptors=[_descriptor(extra=1)])
    with pytest.raises(ValueError, match="integers"):
        capture.capture(
            job_id=1,
            direction="h2d",
            descriptors=[_descriptor(src_offset="0")],
        )
    with pytest.raises(ValueError, match="invalid"):
        capture.capture(
            job_id=1,
            direction="h2d",
            descriptors=[_descriptor(src_offset=-1)],
        )
    with pytest.raises(ValueError, match="invalid"):
        capture.capture(job_id=1, direction="h2d", descriptors=[_descriptor(size=0)])
    with pytest.raises(ValueError, match="empty"):
        capture.capture(job_id=1, direction="h2d", descriptors=[])
    with pytest.raises(ValueError, match="direction"):
        capture.capture(
            job_id=1,
            direction="h2d",
            descriptors=[_descriptor(direction="d2h")],
        )
    with pytest.raises(ValueError, match="unsupported direction"):
        capture.capture(
            job_id=1,
            direction="sideways",
            descriptors=[_descriptor(direction="sideways")],
        )


def test_capture_rejects_second_write_same_job(tmp_path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "replay")
    first = capture.capture(
        job_id=3, direction="d2h", descriptors=[_descriptor(direction="d2h")]
    )
    assert first.exists()
    with pytest.raises(FileExistsError):
        capture.capture(
            job_id=3, direction="d2h", descriptors=[_descriptor(direction="d2h")]
        )


def test_capture_writes_never_contain_addresses(tmp_path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "existing-server-probe")
    output = capture.capture(job_id=4, direction="h2d", descriptors=[_descriptor()])
    text = output.read_text()
    assert DESCRIPTOR_SCHEMA in text
    assert "address" not in text
    assert json.loads(text)["direction"] == "h2d"
    assert json.loads(text)["descriptors"][0]["size"] == 16


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows os.open ignores permission mode bits",
)
def test_capture_writes_mode_0600_on_posix(tmp_path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    output = capture.capture(job_id=5, direction="h2d", descriptors=[_descriptor()])
    assert output.stat().st_mode & 0o777 == 0o600
