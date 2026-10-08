from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import vllm_hust_kv_transfer_observability.plugin as plugin


@pytest.fixture(autouse=True)
def reset_plugin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(plugin, "_REGISTERED_PID", None)
    monkeypatch.setattr(plugin, "_ADAPTER", None)
    monkeypatch.setattr(plugin, "_BINDING", None)
    monkeypatch.delenv("VLLMHUST_EXT_ENABLED_BUNDLES", raising=False)
    monkeypatch.delenv("VLLM_PLUGINS", raising=False)
    monkeypatch.delenv(plugin.EVENT_PATH_ENV, raising=False)


def test_plugin_is_inert_without_explicit_activation(monkeypatch):
    binding = Mock()
    monkeypatch.setattr(plugin, "VllmOffloadingObserverBinding", binding)

    plugin.register_plugin()

    binding.assert_not_called()


def test_plugin_registers_once_and_stops(monkeypatch, tmp_path: Path):
    adapter = Mock()
    adapter.start.return_value = True
    adapter.stop.return_value = True
    adapter_factory = Mock(return_value=adapter)
    binding = Mock()
    monkeypatch.setattr(plugin, "KVTransferHostAdapter", adapter_factory)
    monkeypatch.setattr(
        plugin, "VllmOffloadingObserverBinding", Mock(return_value=binding)
    )
    monkeypatch.setenv("VLLM_PLUGINS", plugin.PLUGIN_NAME)
    monkeypatch.setenv(plugin.EVENT_PATH_ENV, str(tmp_path / "events.jsonl"))

    plugin.register_plugin()
    plugin.register_plugin()

    adapter_factory.assert_called_once()
    adapter.start.assert_called_once_with(binding)
    assert adapter_factory.call_args.args[0].event_path == tmp_path / "events.jsonl"
    assert adapter_factory.call_args.kwargs == {"retain_restore_receipts": False}
    assert plugin.stop_plugin() is True
    adapter.stop.assert_called_once()


def test_default_destination_is_launch_and_process_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("VLLM_ECPA_LAUNCH_ID", "launch-a")

    first = plugin._event_path()
    monkeypatch.setenv("VLLM_ECPA_LAUNCH_ID", "launch-b")
    second = plugin._event_path()

    assert first.parent == tmp_path
    assert first != second
    assert str(plugin.os.getpid()) in first.name
