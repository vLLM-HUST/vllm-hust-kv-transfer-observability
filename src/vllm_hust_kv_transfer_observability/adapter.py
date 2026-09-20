# SPDX-License-Identifier: Apache-2.0
"""Explicit plugin-side binding for a future typed host observer seam."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Protocol

from .config import ObserverConfig
from .descriptors import DescriptorInventory, DescriptorLayoutCapture
from .events import JsonlKVTransferEventSink
from .normalization import LifecycleNormalizer, SourceObservation

HOST_OBSERVER_CONTRACT = "vllm.kv-transfer.observer.v1"

ObservationCallback = Callable[[SourceObservation], bool]
DescriptorCallback = Callable[[DescriptorInventory], bool]


@dataclass(frozen=True, slots=True)
class HostObserverCallbacks:
    """Plugin callbacks passed to a source-specific host binding."""

    observe: ObservationCallback
    descriptor: DescriptorCallback | None


class HostObserverBinding(Protocol):
    """Plugin-owned wrapper around an accepted host registration API."""

    contract_version: str

    def register(self, callbacks: HostObserverCallbacks) -> object:
        """Register callbacks and return a non-null removable handle."""

    def unregister(self, handle: object) -> None:
        """Remove a handle previously returned by ``register``."""


class AdapterActivationError(RuntimeError):
    """The adapter could not safely attach before serving callbacks began."""


class AdapterContractError(AdapterActivationError):
    """The binding contract could not be inspected or was incompatible."""


class AdapterResourceError(AdapterActivationError):
    """Plugin resource initialization failed before host registration."""


class AdapterRegistrationError(AdapterActivationError):
    """Host registration failed or returned no removable handle."""


class AdapterState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class AdapterCounters:
    registrations: int = 0
    observations_received: int = 0
    observations_emitted: int = 0
    normalization_dropped: int = 0
    sink_dropped: int = 0
    descriptors_received: int = 0
    descriptors_written: int = 0
    descriptors_dropped: int = 0
    inactive_dropped: int = 0
    callback_errors: int = 0
    unregister_errors: int = 0
    cleanup_errors: int = 0
    shutdown_timeouts: int = 0


class KVTransferHostAdapter:
    """Join a typed host binding to the bounded plugin observation core.

    Construction and configuration errors fail closed. Once registration has
    started, all callback, normalization, sink, descriptor, and unregister
    failures are contained and counted so they cannot escape into serving.
    """

    def __init__(self, config: ObserverConfig) -> None:
        if type(config) is not ObserverConfig:
            raise TypeError("config must be an ObserverConfig")
        self.config = config
        self._state = AdapterState.STOPPED
        self._state_lock = Lock()
        self._lifecycle_lock = Lock()
        self._counter_values = {field: 0 for field in AdapterCounters.__slots__}
        self._binding: HostObserverBinding | None = None
        self._handle: object | None = None
        self._normalizer: LifecycleNormalizer | None = None
        self._sink: JsonlKVTransferEventSink | None = None
        self._descriptor_capture: DescriptorLayoutCapture | None = None

    @property
    def state(self) -> AdapterState:
        with self._state_lock:
            return self._state

    @property
    def counters(self) -> AdapterCounters:
        with self._state_lock:
            return AdapterCounters(**self._counter_values)

    def _increment(self, name: str) -> None:
        with self._state_lock:
            self._counter_values[name] += 1

    def _prepare_resources(
        self,
    ) -> tuple[
        LifecycleNormalizer,
        JsonlKVTransferEventSink,
        DescriptorLayoutCapture | None,
    ]:
        normalizer = LifecycleNormalizer(
            max_correlated_transfers=self.config.max_correlated_transfers,
            max_recovery_admissions=self.config.max_recovery_admissions,
        )
        sink: JsonlKVTransferEventSink | None = None
        capture: DescriptorLayoutCapture | None = None
        try:
            sink = JsonlKVTransferEventSink(
                self.config.event_path,
                max_pending_records=self.config.max_pending_records,
                max_record_bytes=self.config.max_record_bytes,
                max_file_bytes=self.config.max_file_bytes,
            )
            if self.config.descriptor_dir is not None:
                capture = DescriptorLayoutCapture(
                    self.config.descriptor_dir,
                    self.config.evidence_label,
                    max_regions=self.config.max_descriptor_regions,
                    max_record_bytes=self.config.max_descriptor_record_bytes,
                )
        except Exception:
            self._close_prepared_resources(normalizer, sink, capture)
            raise
        return normalizer, sink, capture

    def start(self, binding: HostObserverBinding) -> bool:
        """Explicitly register; return ``False`` for disabled/idempotent calls."""
        if not self.config.enabled:
            return False
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state is not AdapterState.STOPPED:
                    return False
            try:
                contract_version = getattr(binding, "contract_version", None)
            except Exception as exc:
                raise AdapterContractError(
                    "host binding contract could not be inspected"
                ) from exc
            if contract_version != HOST_OBSERVER_CONTRACT:
                raise AdapterContractError(
                    f"host binding must provide {HOST_OBSERVER_CONTRACT}"
                )
            try:
                normalizer, sink, capture = self._prepare_resources()
            except Exception as exc:
                raise AdapterResourceError(
                    "observer destinations could not be initialized"
                ) from exc

            with self._state_lock:
                self._state = AdapterState.STARTING
                self._normalizer = normalizer
                self._sink = sink
                self._descriptor_capture = capture
            callbacks = HostObserverCallbacks(
                observe=self.observe,
                descriptor=self.capture_descriptor if capture is not None else None,
            )
            try:
                handle = binding.register(callbacks)
                if handle is None:
                    raise ValueError("host binding returned a null handle")
            except Exception as exc:
                self._close_prepared_resources(normalizer, sink, capture)
                with self._state_lock:
                    self._normalizer = None
                    self._sink = None
                    self._descriptor_capture = None
                    self._state = AdapterState.STOPPED
                raise AdapterRegistrationError(
                    "host observer registration failed"
                ) from exc

            with self._state_lock:
                self._binding = binding
                self._handle = handle
                self._state = AdapterState.ACTIVE
                self._counter_values["registrations"] += 1
            return True

    def _close_prepared_resources(
        self,
        normalizer: LifecycleNormalizer,
        sink: JsonlKVTransferEventSink | None,
        capture: DescriptorLayoutCapture | None,
    ) -> bool:
        cleanup_ok = True
        try:
            normalizer.close()
        except Exception:
            cleanup_ok = False
            self._increment("cleanup_errors")
        if capture is not None:
            try:
                capture.close()
            except Exception:
                cleanup_ok = False
                self._increment("cleanup_errors")
        if sink is None:
            return cleanup_ok
        try:
            sink_ok = sink.close(self.config.shutdown_timeout_seconds)
        except Exception:
            self._increment("cleanup_errors")
            return False
        if not sink_ok:
            # The sink returns False only when its writer is still alive.
            # Close exceptions are cleanup errors, not shutdown timeouts.
            self._increment("shutdown_timeouts")
        return cleanup_ok and sink_ok

    def observe(self, source: SourceObservation) -> bool:
        """Normalize and enqueue one typed source record without raising."""
        with self._state_lock:
            if self._state is not AdapterState.ACTIVE:
                self._counter_values["inactive_dropped"] += 1
                return False
            self._counter_values["observations_received"] += 1
            normalizer = self._normalizer
            sink = self._sink
        if normalizer is None or sink is None:
            self._increment("callback_errors")
            return False
        try:
            observation = normalizer.normalize(source)
            if observation is None:
                self._increment("normalization_dropped")
                return False
            if not sink.emit(observation):
                self._increment("sink_dropped")
                return False
        except Exception:
            self._increment("callback_errors")
            return False
        self._increment("observations_emitted")
        return True

    def capture_descriptor(self, inventory: DescriptorInventory) -> bool:
        """Publish one already-sanitized inventory without raising."""
        with self._state_lock:
            if self._state is not AdapterState.ACTIVE:
                self._counter_values["inactive_dropped"] += 1
                return False
            self._counter_values["descriptors_received"] += 1
            capture = self._descriptor_capture
        if capture is None:
            self._increment("descriptors_dropped")
            return False
        try:
            output = capture.capture(inventory)
        except Exception:
            self._increment("callback_errors")
            return False
        counter = "descriptors_written" if output is not None else "descriptors_dropped"
        self._increment(counter)
        return output is not None

    def stop(self) -> bool:
        """Become inert, unregister, and close owned resources idempotently."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state is AdapterState.STOPPED:
                    return True
                self._state = AdapterState.STOPPING
                binding = self._binding
                handle = self._handle
                normalizer = self._normalizer
                sink = self._sink
                capture = self._descriptor_capture

            unregister_ok = True
            if binding is not None and handle is not None:
                try:
                    binding.unregister(handle)
                except Exception:
                    unregister_ok = False
                    self._increment("unregister_errors")

            shutdown_ok = True
            if normalizer is not None and sink is not None:
                shutdown_ok = self._close_prepared_resources(normalizer, sink, capture)
            with self._state_lock:
                self._binding = None
                self._handle = None
                self._normalizer = None
                self._sink = None
                self._descriptor_capture = None
                self._state = AdapterState.STOPPED
            return unregister_ok and shutdown_ok

    def __enter__(self) -> KVTransferHostAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


__all__ = [
    "AdapterActivationError",
    "AdapterContractError",
    "AdapterCounters",
    "AdapterRegistrationError",
    "AdapterResourceError",
    "AdapterState",
    "HOST_OBSERVER_CONTRACT",
    "HostObserverBinding",
    "HostObserverCallbacks",
    "KVTransferHostAdapter",
]
