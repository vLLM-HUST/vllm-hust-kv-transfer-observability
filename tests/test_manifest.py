import importlib
from pathlib import Path

from vllm_hust_ext.manifest import activation_blocker, load_manifest
from vllm_hust_ext.providers.base import assess_compatibility

import vllm_hust_kv_transfer_observability

MANIFEST_PATH = Path(vllm_hust_kv_transfer_observability.__file__).with_name(
    "vllm-hust-extension-v0.3.json"
)


def test_manifest_declares_one_active_versioned_carrier() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.bundle_id == "org.vllm-hust.kv-transfer-observability"
    assert activation_blocker(manifest) is None
    assert manifest.host.api_range == ">=1,<2"
    protocols = {
        protocol.name: protocol.version_range for protocol in manifest.protocols
    }
    assert protocols == {
        "vllm.general_plugins": None,
        "vllm.kv-transfer.observer": "==1.1",
    }
    assert dict(manifest.activation.environment) == {
        "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVENT_PATH": "",
        "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_DESCRIPTOR_DIR": "",
        "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVIDENCE_LABEL": "",
    }
    entry_points = [
        (entry.group, entry.name) for entry in manifest.activation.entry_points
    ]
    assert entry_points == [("vllm.general_plugins", "kv_transfer_observability")]


def test_implementation_ref_and_entry_point_resolve() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    component = manifest.components[0]
    module_name, _, object_name = component.implementation_ref.partition(":")

    module = importlib.import_module(module_name)
    assert callable(getattr(module, object_name))
    assert dict(manifest.implementation[0].attributes) == {
        "group": "vllm.general_plugins",
        "name": "kv_transfer_observability",
        "status": "active",
    }


def test_resource_claim_is_shared_and_process_scoped() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    (claim,) = manifest.resource_claims

    assert claim.resource == "vllm.kv-transfer.observer"
    assert claim.scope == "vllm-process"
    assert claim.mode == "shared"


def test_manifest_rejects_host_observer_v1_0_before_activation() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    kwargs = {
        "detected_host_version": "0.29.1",
        "default_api_version": "1.0",
    }

    incompatible, _ = assess_compatibility(
        manifest,
        {},
        default_protocol_versions={"vllm.kv-transfer.observer": "1.0"},
        **kwargs,
    )
    compatible, _ = assess_compatibility(
        manifest,
        {},
        default_protocol_versions={"vllm.kv-transfer.observer": "1.1"},
        **kwargs,
    )

    assert incompatible is False
    assert compatible is True
