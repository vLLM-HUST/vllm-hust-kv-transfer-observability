import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

import vllm_hust_kv_transfer_observability.adapter as adapter_module
from vllm_hust_kv_transfer_observability import (
    HOST_OBSERVER_CONTRACT,
    AdapterActivationError,
    AdapterContractError,
    AdapterRegistrationError,
    AdapterResourceError,
    AdapterState,
    ComputeKind,
    CoreRecoveryAdmitted,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    DescriptorInventory,
    DescriptorRegion,
    FirstComputeObserved,
    HostObserverCallbacks,
    KVTransferHostAdapter,
    ObservationIdentity,
    ObserverConfig,
    ReceiptIdentity,
    SourceHost,
    TransferDirection,
    TransferIdentity,
    TransferOperation,
)

PROCESS_UUID = "e" * 32


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock, Mock]:
    """Exercise activation failures without depending on POSIX destinations."""
    normalizer, sink, capture = Mock(), Mock(), Mock()
    sink.close.return_value = True
    monkeypatch.setattr(
        adapter_module, "LifecycleNormalizer", Mock(return_value=normalizer)
    )
    monkeypatch.setattr(
        adapter_module, "JsonlKVTransferEventSink", Mock(return_value=sink)
    )
    monkeypatch.setattr(
        adapter_module, "DescriptorLayoutCapture", Mock(return_value=capture)
    )
    return normalizer, sink, capture


class FakeBinding:
    def __init__(
        self,
        *,
        contract_version: str = HOST_OBSERVER_CONTRACT,
        registration_error: Exception | None = None,
        unregister_error: Exception | None = None,
    ) -> None:
        self.contract_version = contract_version
        self.registration_error = registration_error
        self.unregister_error = unregister_error
        self.callbacks: HostObserverCallbacks | None = None
        self.register_calls = 0
        self.unregister_calls = 0
        self.handle = object()

    def register(self, callbacks: HostObserverCallbacks) -> object:
        self.register_calls += 1
        if self.registration_error is not None:
            raise self.registration_error
        self.callbacks = callbacks
        return self.handle

    def unregister(self, handle: object) -> None:
        self.unregister_calls += 1
        assert handle is self.handle
        self.callbacks = None
        if self.unregister_error is not None:
            raise self.unregister_error


class EagerBinding(FakeBinding):
    def __init__(self) -> None:
        super().__init__()
        self.eager_result: bool | None = None

    def register(self, callbacks: HostObserverCallbacks) -> object:
        self.eager_result = callbacks.observe(submitted())
        return super().register(callbacks)


def identity() -> ObservationIdentity:
    return ObservationIdentity("request-1", 2, 0, recovery_epoch=1)


def transfer() -> TransferIdentity:
    return TransferIdentity(f"{PROCESS_UUID}:t:1")


def receipt(sequence: int = 1) -> ReceiptIdentity:
    return ReceiptIdentity(f"{PROCESS_UUID}:k:{sequence}")


def enabled_config(tmp_path: Path, *, descriptors: bool = False) -> ObserverConfig:
    raw: dict[str, object] = {
        "enabled": True,
        "event_path": str(tmp_path / "events.jsonl"),
    }
    if descriptors:
        raw.update(
            descriptor_dir=str(tmp_path / "descriptors"),
            evidence_label="existing-server-probe",
        )
        (tmp_path / "descriptors").mkdir()
    return ObserverConfig.from_mapping(raw)


def submitted() -> CoreTransferSubmitted:
    return CoreTransferSubmitted(
        identity(),
        transfer(),
        TransferOperation.H2D_RESTORE,
        block_count=2,
        observed_at_ns=10,
    )


def descriptor() -> DescriptorInventory:
    return DescriptorInventory(
        identity(),
        transfer(),
        job_id=7,
        direction=TransferDirection.H2D,
        regions=(
            DescriptorRegion(
                0,
                8,
                16,
                TransferDirection.H2D,
                src_region_id=1,
                dst_region_id=2,
            ),
        ),
        observed_at_ns=11,
    )


def test_disabled_adapter_does_no_registration_or_io(tmp_path: Path) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(ObserverConfig())

    assert not adapter.start(binding)
    assert adapter.state is AdapterState.STOPPED
    assert binding.register_calls == 0
    assert not (tmp_path / "events.jsonl").exists()
    assert adapter.stop()


def test_contract_mismatch_fails_before_destinations_or_registration(
    tmp_path: Path,
) -> None:
    binding = FakeBinding(contract_version="vllm.kv-transfer.observer.v2")
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))

    with pytest.raises(AdapterContractError):
        adapter.start(binding)
    assert binding.register_calls == 0
    assert adapter.state is AdapterState.STOPPED
    assert not (tmp_path / "events.jsonl").exists()


def test_invalid_destination_fails_before_host_registration(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "events.jsonl"
    adapter = KVTransferHostAdapter(
        ObserverConfig.from_mapping({"enabled": True, "event_path": str(missing)})
    )
    binding = FakeBinding()

    with pytest.raises(AdapterResourceError):
        adapter.start(binding)
    assert binding.register_calls == 0
    assert adapter.state is AdapterState.STOPPED


def test_registration_failure_closes_prepared_resources(
    tmp_path: Path, resources: tuple[Mock, Mock, Mock]
) -> None:
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    binding = FakeBinding(registration_error=RuntimeError("unavailable"))

    with pytest.raises(AdapterRegistrationError) as error:
        adapter.start(binding)
    assert isinstance(error.value, AdapterActivationError)
    assert error.value.__cause__ is binding.registration_error
    assert adapter.state is AdapterState.STOPPED
    assert binding.register_calls == 1
    for resource in resources:
        resource.close.assert_called_once()


def test_contract_inspection_failure_keeps_original_cause(tmp_path: Path) -> None:
    cause = RuntimeError("contract unavailable")

    class UnreadableBinding:
        @property
        def contract_version(self) -> str:
            raise cause

    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    with pytest.raises(AdapterContractError) as error:
        adapter.start(UnreadableBinding())  # type: ignore[arg-type]
    assert error.value.__cause__ is cause
    assert adapter.state is AdapterState.STOPPED


@pytest.mark.parametrize("failed_resource", ["sink", "capture"])
def test_initialization_cleanup_preserves_cause_and_closes_remaining_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resources: tuple[Mock, Mock, Mock],
    failed_resource: str,
) -> None:
    normalizer, sink, capture = resources
    cause = OSError("initial destination failure")
    normalizer.close.side_effect = RuntimeError("normalizer cleanup failure")
    sink.close.side_effect = OSError("sink cleanup failure")
    factory = (
        "JsonlKVTransferEventSink"
        if failed_resource == "sink"
        else "DescriptorLayoutCapture"
    )
    monkeypatch.setattr(adapter_module, factory, Mock(side_effect=cause))
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    binding = FakeBinding()

    with pytest.raises(AdapterResourceError) as error:
        adapter.start(binding)
    assert error.value.__cause__ is cause
    assert adapter.state is AdapterState.STOPPED
    assert binding.register_calls == 0
    normalizer.close.assert_called_once()
    capture.close.assert_not_called()
    if failed_resource == "sink":
        sink.close.assert_not_called()
        assert adapter.counters.cleanup_errors == 1
    else:
        sink.close.assert_called_once()
        assert adapter.counters.cleanup_errors == 2
    assert adapter.counters.shutdown_timeouts == 0


def test_registration_error_survives_multiple_cleanup_errors(
    tmp_path: Path, resources: tuple[Mock, Mock, Mock]
) -> None:
    for resource in resources:
        resource.close.side_effect = OSError("cleanup failed")
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    binding = FakeBinding(registration_error=RuntimeError("registration failed"))
    with pytest.raises(AdapterRegistrationError) as error:
        adapter.start(binding)
    assert error.value.__cause__ is binding.registration_error
    for resource in resources:
        resource.close.assert_called_once()
    assert adapter.counters.cleanup_errors == 3
    assert adapter.counters.shutdown_timeouts == 0
    assert adapter.state is AdapterState.STOPPED


@pytest.mark.parametrize("close_result", [True, False, OSError("close failed")])
def test_shutdown_counts_timeout_and_cleanup_error_separately(
    tmp_path: Path, resources: tuple[Mock, Mock, Mock], close_result: bool | Exception
) -> None:
    _, sink, _ = resources
    if isinstance(close_result, Exception):
        sink.close.side_effect = close_result
    else:
        sink.close.return_value = close_result
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    binding = FakeBinding()
    assert adapter.start(binding)
    assert adapter.stop() is (close_result is True)
    assert adapter.counters.cleanup_errors == int(isinstance(close_result, Exception))
    assert adapter.counters.shutdown_timeouts == int(close_result is False)
    assert binding.unregister_calls == 1
    assert adapter.state is AdapterState.STOPPED
    assert adapter.stop()
    sink.close.assert_called_once()


def test_partial_initialization_failure_stops_real_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    normalizer = adapter_module.LifecycleNormalizer()
    normalizer.close = Mock(side_effect=RuntimeError("cleanup failed"))
    monkeypatch.setattr(adapter_module, "LifecycleNormalizer", lambda **_: normalizer)
    cause = OSError("descriptor initialization failed")
    monkeypatch.setattr(
        adapter_module, "DescriptorLayoutCapture", Mock(side_effect=cause)
    )
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    with pytest.raises(AdapterResourceError) as error:
        adapter.start(FakeBinding())
    assert error.value.__cause__ is cause
    assert adapter.counters.cleanup_errors == 1
    assert not any(
        thread.name == "kv-transfer-jsonl-sink" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_writer_io_failure_is_not_a_shutdown_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    assert adapter.start(FakeBinding())
    sink = adapter._sink
    assert sink is not None
    monkeypatch.setattr(sink, "_write_record", Mock(side_effect=OSError("disk full")))
    assert adapter.observe(submitted())
    assert sink.flush()
    assert adapter.stop()
    assert sink.counters.io_errors == 1
    assert adapter.counters.shutdown_timeouts == 0
    assert adapter.counters.cleanup_errors == 0


def test_callback_before_registration_completes_is_dropped(tmp_path: Path) -> None:
    binding = EagerBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))

    assert adapter.start(binding)
    assert binding.eager_result is False
    assert adapter.counters.inactive_dropped == 1
    assert adapter.stop()
    assert not (tmp_path / "events.jsonl").exists()


def test_descriptor_callback_is_not_registered_when_not_configured(
    tmp_path: Path,
) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))

    assert adapter.start(binding)
    assert binding.callbacks is not None
    assert binding.callbacks.descriptor is None
    assert adapter.stop()


def test_typed_callbacks_reach_event_and_descriptor_sinks(tmp_path: Path) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))

    assert adapter.start(binding)
    assert binding.callbacks is not None
    assert binding.callbacks.descriptor is not None
    assert binding.callbacks.observe(submitted())
    assert binding.callbacks.descriptor(descriptor())
    assert adapter.stop()

    records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    assert [record["event"] for record in records] == ["restore_started"]
    assert records[0]["identity"]["request_id"] == "request-1"
    descriptor_files = list((tmp_path / "descriptors").glob("*.json"))
    assert len(descriptor_files) == 1
    inventory = json.loads(descriptor_files[0].read_text())
    assert inventory["transfer_id"] == transfer().value
    assert "address" not in json.dumps((records, inventory))
    assert adapter.counters.observations_emitted == 1
    assert adapter.counters.descriptors_written == 1
    assert binding.unregister_calls == 1


def test_recovery_chain_keeps_exact_identity_through_adapter(tmp_path: Path) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    assert adapter.start(binding)
    assert binding.callbacks is not None

    for source in (
        submitted(),
        CoreTransferCompleted(
            transfer(),
            observed_at_ns=20,
            success=True,
            bytes_moved=1024,
            receipt=receipt(),
        ),
        CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=30),
        FirstComputeObserved(
            SourceHost.VLLM_ASCEND,
            identity(),
            receipt(2),
            (transfer(),),
            ComputeKind.DECODE,
            observed_at_ns=40,
        ),
    ):
        binding.callbacks.observe(source)
    assert adapter.stop()

    records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    assert [record["event"] for record in records] == [
        "restore_started",
        "restore_completed",
        "recovery_admitted",
        "first_compute",
    ]
    assert all(record["identity"] == records[0]["identity"] for record in records)


def test_invalid_callback_data_is_dropped_without_escaping(tmp_path: Path) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    assert adapter.start(binding)
    assert binding.callbacks is not None
    assert binding.callbacks.descriptor is not None

    binding.callbacks.observe({"event": "restore_started"})  # type: ignore[arg-type]
    binding.callbacks.descriptor({"address": 123})  # type: ignore[arg-type]
    assert adapter.stop()
    assert adapter.counters.normalization_dropped == 1
    assert adapter.counters.descriptors_dropped == 1
    assert not (tmp_path / "events.jsonl").exists()
    assert not list((tmp_path / "descriptors").iterdir())


def test_callback_exceptions_are_contained_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    assert adapter.start(binding)
    assert adapter._sink is not None
    assert adapter._descriptor_capture is not None

    monkeypatch.setattr(
        adapter._sink, "emit", lambda _observation: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(
        adapter._descriptor_capture,
        "capture",
        lambda _inventory: (_ for _ in ()).throw(OSError()),
    )
    assert not adapter.observe(submitted())
    assert not adapter.capture_descriptor(descriptor())
    assert adapter.stop()
    assert adapter.counters.callback_errors == 2


def test_stop_is_idempotent_and_callbacks_become_inert(tmp_path: Path) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    assert adapter.start(binding)
    callbacks = binding.callbacks
    assert callbacks is not None
    assert not adapter.start(binding)

    assert adapter.stop()
    assert adapter.stop()
    callbacks.observe(submitted())
    assert binding.unregister_calls == 1
    assert adapter.counters.registrations == 1
    assert adapter.counters.inactive_dropped == 1
    assert not any(
        thread.name == "kv-transfer-jsonl-sink" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_unregister_failure_does_not_skip_resource_cleanup(tmp_path: Path) -> None:
    binding = FakeBinding(unregister_error=RuntimeError("host failure"))
    adapter = KVTransferHostAdapter(enabled_config(tmp_path))
    assert adapter.start(binding)

    assert not adapter.stop()
    assert adapter.state is AdapterState.STOPPED
    assert adapter.counters.unregister_errors == 1
    assert not any(
        thread.name == "kv-transfer-jsonl-sink" and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_cleanup_exception_is_contained_and_other_resources_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = FakeBinding()
    adapter = KVTransferHostAdapter(enabled_config(tmp_path, descriptors=True))
    assert adapter.start(binding)
    assert adapter._descriptor_capture is not None
    real_close = adapter._descriptor_capture.close

    def close_then_fail() -> None:
        real_close()
        raise RuntimeError("capture cleanup failed")

    monkeypatch.setattr(adapter._descriptor_capture, "close", close_then_fail)
    assert not adapter.stop()
    assert adapter.state is AdapterState.STOPPED
    assert adapter.counters.cleanup_errors == 1
    assert not any(
        thread.name == "kv-transfer-jsonl-sink" and thread.is_alive()
        for thread in threading.enumerate()
    )
