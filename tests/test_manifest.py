from pathlib import Path

from vllm_hust_ext.manifest import activation_blocker, load_manifest

import vllm_hust_kv_transfer_observability


def test_descriptor_is_discoverable_but_not_activatable() -> None:
    manifest = load_manifest(
        Path(vllm_hust_kv_transfer_observability.__file__).with_name(
            "vllm-hust-extension-v0.2.json"
        )
    )
    assert manifest.bundle_id == "org.vllm-hust.kv-transfer-observability"
    assert activation_blocker(manifest) is not None


def test_manifest_contract_versions_match_serialized_schemas() -> None:
    manifest = load_manifest(
        Path(vllm_hust_kv_transfer_observability.__file__).with_name(
            "vllm-hust-extension-v0.2.json"
        )
    )
    contracts = {
        contract
        for component in manifest.components
        for contract in component.contracts
    }
    protocols = {
        protocol.name: protocol.version_range for protocol in manifest.protocols
    }
    assert "vllm.kv-transfer.events.v2" in contracts
    assert "vllm.kv-transfer.descriptors.v2" in contracts
    assert protocols["vllm.kv-transfer.events"] == ">=2,<3"
    assert protocols["vllm.kv-transfer.descriptors"] == ">=2,<3"
