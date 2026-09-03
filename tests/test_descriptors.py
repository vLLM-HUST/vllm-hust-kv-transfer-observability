import json
import os
import stat
import threading
from pathlib import Path

import pytest

import vllm_hust_kv_transfer_observability.descriptors as descriptors_module
from vllm_hust_kv_transfer_observability import (
    DescriptorInventory,
    DescriptorLayoutCapture,
    DescriptorRegion,
    ObservationIdentity,
    TransferDirection,
    TransferIdentity,
)

PROCESS_UUID = "c" * 32


def inventory(
    job_id: int = 1,
    *,
    region_count: int = 1,
) -> DescriptorInventory:
    return DescriptorInventory(
        identity=ObservationIdentity("request-1", 7, 1, recovery_epoch=3),
        transfer=TransferIdentity(f"{PROCESS_UUID}:t:{job_id + 1}"),
        job_id=job_id,
        direction=TransferDirection.H2D,
        regions=tuple(
            DescriptorRegion(
                src_offset=index * 16,
                dst_offset=index * 16 + 8,
                size=16,
                direction=TransferDirection.H2D,
                src_region_id=index,
                dst_region_id=index,
            )
            for index in range(region_count)
        ),
        observed_at_ns=30,
    )


def test_descriptor_capture_is_correlated_address_free_and_atomic(
    tmp_path: Path,
) -> None:
    with DescriptorLayoutCapture(tmp_path, "real-online") as capture:
        output = capture.capture(inventory())
        assert output is not None
        payload = json.loads(output.read_text())
        assert payload["schema"] == "vllm-hust.kv-transfer-descriptor-layout.v2"
        assert payload["identity"]["worker_generation"] == 7
        assert payload["transfer_id"] == f"{PROCESS_UUID}:t:2"
        assert payload["descriptors"][0]["size"] == 16
        assert payload["descriptors"][0]["src_region_id"] == 0
        assert payload["descriptors"][0]["dst_region_id"] == 0
        assert "address" not in json.dumps(payload)
        assert "payload" not in json.dumps(payload)
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        assert not list(tmp_path.glob("*.tmp"))
        assert capture.counters.written == 1


@pytest.mark.parametrize("job_id", [True, -1, "../../escape"])
def test_descriptor_inventory_rejects_unsafe_job_ids(job_id) -> None:
    with pytest.raises(ValueError, match="job_id"):
        DescriptorInventory(
            identity=ObservationIdentity("request", 0, 0),
            transfer=TransferIdentity(f"{PROCESS_UUID}:t:1"),
            job_id=job_id,
            direction=TransferDirection.D2H,
            regions=(DescriptorRegion(0, 0, 1, TransferDirection.D2H, 0, 0),),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("src_region_id", True),
        ("src_region_id", -1),
        ("src_region_id", 2**32),
        ("dst_region_id", "dst_tensor_0"),
        ("dst_region_id", -1),
        ("dst_region_id", 2**32),
    ],
)
def test_descriptor_region_identity_is_bounded(field: str, value: object) -> None:
    fields = {
        "src_offset": 0,
        "dst_offset": 0,
        "size": 1,
        "direction": TransferDirection.D2H,
        "src_region_id": 0,
        "dst_region_id": 0,
    }
    fields[field] = value
    with pytest.raises(ValueError, match=field):
        DescriptorRegion(**fields)  # type: ignore[arg-type]


def test_capture_rejects_untyped_records_without_raising(tmp_path: Path) -> None:
    with DescriptorLayoutCapture(tmp_path, "replay") as capture:
        assert capture.capture({"src_address": 1}) is None  # type: ignore[arg-type]
        assert capture.counters.invalid_records == 1
        assert not list(tmp_path.iterdir())


def test_capture_rejects_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        DescriptorLayoutCapture(link, "real-online")


def test_capture_detects_directory_replacement(tmp_path: Path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    moved = tmp_path.with_name(f"{tmp_path.name}-moved")
    tmp_path.rename(moved)
    tmp_path.mkdir()
    assert capture.capture(inventory()) is None
    capture.close()
    assert capture.counters.io_errors == 1
    assert not list(tmp_path.iterdir())
    assert not list(moved.iterdir())


def test_descriptor_capacity_bounds_are_counted(tmp_path: Path) -> None:
    with DescriptorLayoutCapture(
        tmp_path,
        "simulation/model",
        max_regions=1,
        max_record_bytes=100,
    ) as capture:
        assert capture.capture(inventory(region_count=2)) is None
        assert capture.capture(inventory(job_id=2)) is None
        assert capture.counters.capacity_dropped == 2
        assert not list(tmp_path.iterdir())


def test_descriptor_short_writes_are_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = os.write
    calls = 0

    def short_write(descriptor: int, data) -> int:
        nonlocal calls
        calls += 1
        chunk = bytes(data[: max(1, len(data) // 2)])
        return real_write(descriptor, chunk)

    monkeypatch.setattr(descriptors_module.os, "write", short_write)
    with DescriptorLayoutCapture(tmp_path, "existing-server-probe") as capture:
        output = capture.capture(inventory())
    assert output is not None
    assert calls > 1
    assert json.loads(output.read_text())["job_id"] == 1


def test_partial_write_failure_leaves_no_published_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = os.write
    calls = 0

    def fail_after_partial(descriptor: int, data) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, bytes(data[:10]))
        raise OSError("disk full")

    monkeypatch.setattr(descriptors_module.os, "write", fail_after_partial)
    with DescriptorLayoutCapture(tmp_path, "real-online") as capture:
        assert capture.capture(inventory()) is None
        assert capture.counters.io_errors == 1
    assert not list(tmp_path.iterdir())


def test_failed_publication_rollback_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    real_unlink = os.unlink
    calls = 0

    def fail_publication_cleanup(path, *, dir_fd=None) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise OSError("cleanup unavailable")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(descriptors_module.os, "unlink", fail_publication_cleanup)
    assert capture.capture(inventory()) is None
    capture.close()
    assert capture.counters.io_errors == 2
    assert calls == 3


def test_existing_file_or_symlink_is_never_overwritten(tmp_path: Path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    expected = tmp_path / "rank1-gen7-h2d-job1.json"
    target = tmp_path / "protected"
    target.write_text("protected")
    expected.symlink_to(target)
    assert capture.capture(inventory()) is None
    capture.close()
    assert target.read_text() == "protected"
    assert expected.is_symlink()
    assert capture.counters.conflicts == 1


def test_concurrent_same_inventory_publishes_once(tmp_path: Path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    results: list[Path | None] = []

    def run() -> None:
        results.append(capture.capture(inventory()))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    capture.close()
    assert sum(result is not None for result in results) == 1
    assert capture.counters.written == 1
    assert capture.counters.conflicts == 3
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_capture_after_close_is_fail_open(tmp_path: Path) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    capture.close()
    assert capture.capture(inventory()) is None
    assert capture.counters.closed_dropped == 1


def test_close_waits_for_active_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = DescriptorLayoutCapture(tmp_path, "real-online")
    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    real_write_all = capture._write_all

    def blocked_write_all(descriptor: int, data: bytes) -> None:
        started.set()
        assert release.wait(2)
        real_write_all(descriptor, data)

    monkeypatch.setattr(capture, "_write_all", blocked_write_all)
    capture_thread = threading.Thread(target=lambda: capture.capture(inventory()))
    close_thread = threading.Thread(target=lambda: (capture.close(), closed.set()))
    capture_thread.start()
    assert started.wait(2)
    close_thread.start()
    assert not closed.wait(0.05)
    release.set()
    capture_thread.join()
    close_thread.join()
    assert closed.is_set()
    assert capture.counters.written == 1
    assert not list(tmp_path.glob("*.tmp"))
