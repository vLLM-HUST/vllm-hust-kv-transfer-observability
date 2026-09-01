import json

import pytest

from vllm_hust_kv_transfer_observability import (
    DescriptorLayoutCapture,
    JsonlKVTransferEventSink,
)


def test_event_sink_is_explicit_and_default_off(tmp_path) -> None:
    JsonlKVTransferEventSink(None).emit("ignored", "request")
    output = tmp_path / "events.jsonl"
    with JsonlKVTransferEventSink(output) as sink:
        sink.emit("restore_done", "request-1", bytes_moved=16)
    payload = json.loads(output.read_text())
    assert payload["schema"] == "vllm-hust.kv-transfer-event.v1"
    assert payload["fields"]["bytes_moved"] == 16


def test_descriptor_capture_rejects_addresses_and_writes_offsets(tmp_path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    with pytest.raises(ValueError, match="fields"):
        capture.capture(
            job_id=1,
            direction="h2d",
            descriptors=[
                {
                    "src_offset": 0,
                    "dst_offset": 8,
                    "size": 16,
                    "direction": "h2d",
                    "src_address": 123,
                }
            ],
        )
    output = capture.capture(
        job_id=2,
        direction="h2d",
        descriptors=[
            {"src_offset": 0, "dst_offset": 8, "size": 16, "direction": "h2d"}
        ],
    )
    payload = json.loads(output.read_text())
    assert payload["descriptors"][0]["size"] == 16
    assert "address" not in json.dumps(payload)
