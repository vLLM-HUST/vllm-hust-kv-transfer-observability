# SPDX-License-Identifier: Apache-2.0
"""Explicit vLLM general-plugin activation for KV transfer observation."""

from __future__ import annotations

import atexit
import hashlib
import os
import tempfile
from pathlib import Path

from .adapter import KVTransferHostAdapter
from .config import ObserverConfig
from .native import VllmOffloadingObserverBinding

EXTENSION_ID = "org.vllm-hust.kv-transfer-observability"
PLUGIN_NAME = "kv_transfer_observability"
EVENT_PATH_ENV = "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVENT_PATH"
_ENABLED_BUNDLES_ENV = "VLLMHUST_EXT_ENABLED_BUNDLES"
_VLLM_PLUGINS_ENV = "VLLM_PLUGINS"

_REGISTERED_PID: int | None = None
_ADAPTER: KVTransferHostAdapter | None = None
_BINDING: VllmOffloadingObserverBinding | None = None


def _activation_requested() -> bool:
    bundles = os.getenv(_ENABLED_BUNDLES_ENV)
    if bundles is not None:
        return EXTENSION_ID in {item.strip() for item in bundles.split(",")}
    return PLUGIN_NAME in {
        item.strip() for item in os.getenv(_VLLM_PLUGINS_ENV, "").split(",")
    }


def _event_path() -> Path:
    configured = os.getenv(EVENT_PATH_ENV, "").strip()
    if configured:
        return Path(configured)
    launch_id = os.getenv("VLLM_ECPA_LAUNCH_ID", "unbound")
    launch_digest = hashlib.sha256(launch_id.encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / (
        f"vllm-hust-kv-transfer-{launch_digest}-{os.getpid()}.jsonl"
    )


def stop_plugin() -> bool:
    """Unregister and close process-owned resources idempotently."""

    global _ADAPTER, _BINDING, _REGISTERED_PID
    adapter = _ADAPTER
    if adapter is None:
        return True
    stopped = adapter.stop()
    if stopped:
        _ADAPTER = None
        _BINDING = None
        _REGISTERED_PID = None
    return stopped


def register_plugin() -> None:
    """Attach only after explicit ECPA or direct vLLM activation intent."""

    global _ADAPTER, _BINDING, _REGISTERED_PID
    process_id = os.getpid()
    if process_id == _REGISTERED_PID or not _activation_requested():
        return

    config = ObserverConfig(enabled=True, event_path=_event_path())
    binding = VllmOffloadingObserverBinding()
    adapter = KVTransferHostAdapter(config)
    if not adapter.start(binding):
        raise RuntimeError("KV transfer observer did not activate")
    _ADAPTER = adapter
    _BINDING = binding
    _REGISTERED_PID = process_id
    atexit.register(stop_plugin)


__all__ = [
    "EVENT_PATH_ENV",
    "EXTENSION_ID",
    "PLUGIN_NAME",
    "register_plugin",
    "stop_plugin",
]
