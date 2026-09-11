# SPDX-License-Identifier: Apache-2.0
"""Bounded asynchronous JSONL sink for typed KV lifecycle observations."""

from __future__ import annotations

import json
import math
import os
import queue
import stat
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .schema import KVTransferObservation

try:
    import fcntl
except ImportError:  # pragma: no cover - the supported hosts are POSIX
    fcntl = None  # type: ignore[assignment]

DEFAULT_MAX_PENDING_RECORDS: Final = 4096
DEFAULT_MAX_RECORD_BYTES: Final = 1024 * 1024
DEFAULT_MAX_FILE_BYTES: Final = 64 * 1024 * 1024
_MIN_RECORD_BYTES: Final = 512


@dataclass(frozen=True, slots=True)
class EventSinkCounters:
    enqueued: int = 0
    written: int = 0
    queue_dropped: int = 0
    record_size_dropped: int = 0
    file_capacity_dropped: int = 0
    serialization_errors: int = 0
    io_errors: int = 0
    closed_dropped: int = 0
    shutdown_timeouts: int = 0


class JsonlKVTransferEventSink:
    """Write typed records off the caller path using a bounded queue.

    Invalid constructor configuration fails closed. Once constructed, record,
    queue, and filesystem failures are counted and returned as ``False`` rather
    than raised into the serving path.
    """

    def __init__(
        self,
        path: str | Path | None,
        *,
        max_pending_records: int = DEFAULT_MAX_PENDING_RECORDS,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.path = self._validate_destination(Path(path)) if path is not None else None
        self.max_pending_records = self._positive_int(
            max_pending_records, "max_pending_records"
        )
        self.max_record_bytes = self._positive_int(max_record_bytes, "max_record_bytes")
        self.max_file_bytes = self._positive_int(max_file_bytes, "max_file_bytes")
        if self.max_record_bytes < _MIN_RECORD_BYTES:
            raise ValueError(f"max_record_bytes must be at least {_MIN_RECORD_BYTES}")
        if self.max_file_bytes < self.max_record_bytes:
            raise ValueError("max_file_bytes must be >= max_record_bytes")

        self._state_lock = threading.Lock()
        self._counter_values = {field: 0 for field in EventSinkCounters.__slots__}
        self._records: queue.Queue[bytes] = queue.Queue(self.max_pending_records)
        self._stop = threading.Event()
        self._closed = False
        self._file_descriptor: int | None = None
        self._directory_fd: int | None = None
        self._worker: threading.Thread | None = None

        if self.path is not None:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            self._directory_fd = os.open(self.path.parent, directory_flags)
            self._worker = threading.Thread(
                target=self._run,
                name="kv-transfer-jsonl-sink",
                daemon=True,
            )
            self._worker.start()

    @staticmethod
    def _positive_int(value: object, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _validate_destination(path: Path) -> Path:
        parent = path.parent
        if not parent.is_dir():
            raise ValueError(f"event destination parent is not a directory: {parent}")
        if path.is_symlink():
            raise ValueError("event destination must not be a symlink")
        if path.exists() and not path.is_file():
            raise ValueError("event destination must be a regular file")
        return parent.resolve(strict=True) / path.name

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @property
    def counters(self) -> EventSinkCounters:
        with self._state_lock:
            return EventSinkCounters(**self._counter_values)

    def _increment(self, name: str) -> None:
        with self._state_lock:
            self._counter_values[name] += 1

    def emit(self, observation: KVTransferObservation) -> bool:
        """Queue one record without allowing observation failure to escape."""
        if not self.enabled:
            return False
        try:
            if type(observation) is not KVTransferObservation:
                raise TypeError("observation must be a KVTransferObservation")
            encoded = (
                json.dumps(
                    observation.to_payload(),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("ascii")
        except (TypeError, ValueError, OverflowError):
            self._increment("serialization_errors")
            return False
        if len(encoded) > self.max_record_bytes:
            self._increment("record_size_dropped")
            return False
        with self._state_lock:
            if self._closed:
                self._counter_values["closed_dropped"] += 1
                return False
            try:
                self._records.put_nowait(encoded)
            except queue.Full:
                self._counter_values["queue_dropped"] += 1
                return False
            self._counter_values["enqueued"] += 1
            return True

    def _open_descriptor(self) -> int:
        if self.path is None:
            raise RuntimeError("event sink is disabled")
        if self._directory_fd is None:
            raise RuntimeError("event destination directory is closed")
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            self.path.name,
            flags,
            0o600,
            dir_fd=self._directory_fd,
        )
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            os.close(descriptor)
            raise OSError("event destination is no longer a regular file")
        return descriptor

    def _write_all(self, descriptor: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("event destination made no write progress")
            view = view[written:]

    def _write_record(self, data: bytes) -> str:
        if self._file_descriptor is None:
            self._file_descriptor = self._open_descriptor()
        descriptor = self._file_descriptor
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        start_size: int | None = None
        try:
            start_size = os.fstat(descriptor).st_size
            if start_size + len(data) > self.max_file_bytes:
                return "capacity"
            try:
                self._write_all(descriptor, data)
            except OSError:
                with suppress(OSError):
                    os.ftruncate(descriptor, start_size)
                raise
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        return "written"

    def _run(self) -> None:
        try:
            while not self._stop.is_set() or not self._records.empty():
                try:
                    record = self._records.get(timeout=0.05)
                except queue.Empty:
                    continue
                try:
                    result = self._write_record(record)
                except (OSError, RuntimeError):
                    self._increment("io_errors")
                    if self._file_descriptor is not None:
                        with suppress(OSError):
                            os.close(self._file_descriptor)
                        self._file_descriptor = None
                else:
                    self._increment(
                        "written" if result == "written" else "file_capacity_dropped"
                    )
                finally:
                    self._records.task_done()
        finally:
            if self._file_descriptor is not None:
                with suppress(OSError):
                    os.close(self._file_descriptor)
                self._file_descriptor = None
            if self._directory_fd is not None:
                with suppress(OSError):
                    os.close(self._directory_fd)
                self._directory_fd = None

    def flush(self, timeout: float = 5.0) -> bool:
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number")
        deadline = time.monotonic() + timeout
        while self._records.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)
        return True

    def close(self, timeout: float = 5.0) -> bool:
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number")
        with self._state_lock:
            if self._closed:
                worker = self._worker
            else:
                self._closed = True
                self._stop.set()
                worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        if worker.is_alive():
            self._increment("shutdown_timeouts")
            return False
        return True

    def __enter__(self) -> JsonlKVTransferEventSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_PENDING_RECORDS",
    "DEFAULT_MAX_RECORD_BYTES",
    "EventSinkCounters",
    "JsonlKVTransferEventSink",
]
