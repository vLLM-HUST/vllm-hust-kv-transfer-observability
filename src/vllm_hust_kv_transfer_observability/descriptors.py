# SPDX-License-Identifier: Apache-2.0
"""Atomic address-free descriptor inventory capture."""

from __future__ import annotations

import json
import os
import secrets
import stat
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from .schema import (
    UINT64_MAX,
    ObservationIdentity,
    TransferDirection,
    TransferIdentity,
)

MAX_DESCRIPTOR_REGIONS: Final = 4096
DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES: Final = 1024 * 1024


class EvidenceLabel(str, Enum):
    EXISTING_SERVER_PROBE = "existing-server-probe"
    REAL_ONLINE = "real-online"
    REPLAY = "replay"
    SIMULATION_MODEL = "simulation/model"


ALLOWED_EVIDENCE_LABELS = frozenset(item.value for item in EvidenceLabel)


def _require_uint(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise ValueError(f"{name} must be a uint64")
    return value


@dataclass(frozen=True, slots=True)
class DescriptorRegion:
    """One region-relative copy descriptor without an address or payload."""

    src_offset: int
    dst_offset: int
    size: int
    direction: TransferDirection

    def __post_init__(self) -> None:
        _require_uint(self.src_offset, "src_offset")
        _require_uint(self.dst_offset, "dst_offset")
        _require_uint(self.size, "size")
        if self.size == 0:
            raise ValueError("size must be positive")
        if type(self.direction) is not TransferDirection:
            raise ValueError("direction must be a TransferDirection")

    def to_payload(self) -> dict[str, int | str]:
        return {
            "direction": self.direction.value,
            "dst_offset": self.dst_offset,
            "size": self.size,
            "src_offset": self.src_offset,
        }


@dataclass(frozen=True, slots=True)
class DescriptorInventory:
    """Immutable, correlated descriptor inventory for one transfer job."""

    identity: ObservationIdentity
    transfer: TransferIdentity
    job_id: int
    direction: TransferDirection
    regions: tuple[DescriptorRegion, ...]
    observed_at_ns: int = field(default_factory=time.monotonic_ns)

    def __post_init__(self) -> None:
        if type(self.identity) is not ObservationIdentity:
            raise ValueError("identity must be an ObservationIdentity")
        if type(self.transfer) is not TransferIdentity:
            raise ValueError("transfer must be a TransferIdentity")
        _require_uint(self.job_id, "job_id")
        if type(self.direction) is not TransferDirection:
            raise ValueError("direction must be a TransferDirection")
        if type(self.regions) is not tuple or not self.regions:
            raise ValueError("regions must be a nonempty tuple")
        if len(self.regions) > MAX_DESCRIPTOR_REGIONS:
            raise ValueError("regions exceeds the bounded inventory limit")
        if any(type(region) is not DescriptorRegion for region in self.regions):
            raise ValueError("regions contains an invalid descriptor")
        if any(region.direction is not self.direction for region in self.regions):
            raise ValueError("descriptor direction changed within one inventory")
        _require_uint(self.observed_at_ns, "observed_at_ns")

    def to_payload(self, evidence_label: EvidenceLabel) -> dict[str, object]:
        return {
            "descriptors": [region.to_payload() for region in self.regions],
            "direction": self.direction.value,
            "evidence_label": evidence_label.value,
            "identity": self.identity.to_payload(),
            "job_id": self.job_id,
            "observed_at_ns": self.observed_at_ns,
            "schema": "vllm-hust.kv-transfer-descriptor-layout.v2",
            "transfer_id": self.transfer.value,
        }


@dataclass(frozen=True, slots=True)
class DescriptorCaptureCounters:
    written: int = 0
    invalid_records: int = 0
    capacity_dropped: int = 0
    conflicts: int = 0
    io_errors: int = 0
    closed_dropped: int = 0


class DescriptorLayoutCapture:
    """Publish inventories atomically inside one prevalidated directory."""

    def __init__(
        self,
        capture_dir: str | Path,
        evidence_label: str | EvidenceLabel,
        *,
        max_regions: int = MAX_DESCRIPTOR_REGIONS,
        max_record_bytes: int = DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES,
    ) -> None:
        requested_dir = Path(capture_dir)
        if requested_dir.is_symlink() or not requested_dir.is_dir():
            raise ValueError(
                f"capture directory must be a real directory: {requested_dir}"
            )
        self.capture_dir = requested_dir.resolve(strict=True)
        try:
            self.evidence_label = EvidenceLabel(evidence_label)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported evidence label: {evidence_label}") from exc
        if (
            type(max_regions) is not int
            or not 0 < max_regions <= MAX_DESCRIPTOR_REGIONS
        ):
            raise ValueError(f"max_regions must be within 1..{MAX_DESCRIPTOR_REGIONS}")
        if type(max_record_bytes) is not int or max_record_bytes <= 0:
            raise ValueError("max_record_bytes must be a positive integer")
        self.max_regions = max_regions
        self.max_record_bytes = max_record_bytes
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        self._directory_fd = os.open(self.capture_dir, flags)
        directory_stat = os.fstat(self._directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            os.close(self._directory_fd)
            raise ValueError("capture directory changed during initialization")
        self._directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._counter_values = {
            field: 0 for field in DescriptorCaptureCounters.__slots__
        }
        self._closed = False

    @property
    def counters(self) -> DescriptorCaptureCounters:
        with self._state_lock:
            return DescriptorCaptureCounters(**self._counter_values)

    def _increment(self, name: str) -> None:
        with self._state_lock:
            self._counter_values[name] += 1

    @staticmethod
    def _write_all(descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("descriptor destination made no write progress")
            view = view[written:]

    def capture(self, inventory: DescriptorInventory) -> Path | None:
        """Capture a valid inventory; runtime failures are counted and dropped."""
        with self._operation_lock:
            return self._capture_locked(inventory)

    def _capture_locked(self, inventory: DescriptorInventory) -> Path | None:
        with self._state_lock:
            if self._closed:
                self._counter_values["closed_dropped"] += 1
                return None
        try:
            current_stat = os.stat(self.capture_dir, follow_symlinks=False)
        except OSError:
            self._increment("io_errors")
            return None
        if (current_stat.st_dev, current_stat.st_ino) != self._directory_identity:
            self._increment("io_errors")
            return None
        if type(inventory) is not DescriptorInventory:
            self._increment("invalid_records")
            return None
        if len(inventory.regions) > self.max_regions:
            self._increment("capacity_dropped")
            return None
        try:
            data = (
                json.dumps(
                    inventory.to_payload(self.evidence_label),
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("ascii")
        except (TypeError, ValueError, OverflowError):
            self._increment("invalid_records")
            return None
        if len(data) > self.max_record_bytes:
            self._increment("capacity_dropped")
            return None

        final_name = (
            f"rank{inventory.identity.rank}-gen{inventory.identity.worker_generation}-"
            f"{inventory.direction.value}-job{inventory.job_id}.json"
        )
        try:
            temporary_name = f".{final_name}.{secrets.token_hex(8)}.tmp"
        except OSError:
            self._increment("io_errors")
            return None
        descriptor: int | None = None
        temporary_exists = False
        published = False
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=self._directory_fd,
            )
            temporary_exists = True
            self._write_all(descriptor, data)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.link(
                temporary_name,
                final_name,
                src_dir_fd=self._directory_fd,
                dst_dir_fd=self._directory_fd,
                follow_symlinks=False,
            )
            published = True
            os.unlink(temporary_name, dir_fd=self._directory_fd)
            temporary_exists = False
            os.fsync(self._directory_fd)
        except FileExistsError:
            self._increment("conflicts")
            return None
        except OSError:
            self._increment("io_errors")
            if published:
                try:
                    os.unlink(final_name, dir_fd=self._directory_fd)
                except OSError:
                    self._increment("io_errors")
            return None
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if temporary_exists:
                try:
                    os.unlink(temporary_name, dir_fd=self._directory_fd)
                except OSError:
                    self._increment("io_errors")
        self._increment("written")
        return self.capture_dir / final_name

    def close(self) -> None:
        with self._operation_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                descriptor = self._directory_fd
                self._directory_fd = -1
            try:
                os.close(descriptor)
            except OSError:
                self._increment("io_errors")

    def __enter__(self) -> DescriptorLayoutCapture:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "ALLOWED_EVIDENCE_LABELS",
    "DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES",
    "DescriptorCaptureCounters",
    "DescriptorInventory",
    "DescriptorLayoutCapture",
    "DescriptorRegion",
    "EvidenceLabel",
    "MAX_DESCRIPTOR_REGIONS",
]
