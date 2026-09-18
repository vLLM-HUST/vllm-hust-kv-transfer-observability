import importlib
from pathlib import Path

from vllm_hust_ext.manifest import activation_blocker, load_manifest

import vllm_hust_kv_transfer_observability

MANIFEST_PATH = Path(vllm_hust_kv_transfer_observability.__file__).with_name(
    "vllm-hust-extension-v0.2.json"
)


def test_descriptor_is_discoverable_but_not_activatable() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.bundle_id == "org.vllm-hust.kv-transfer-observability"
    assert activation_blocker(manifest) is not None


def test_manifest_contract_versions_match_serialized_schemas() -> None:
    manifest = load_manifest(MANIFEST_PATH)
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


def test_all_implementation_refs_resolve() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    for carrier in manifest.implementation:
        assert carrier.type == "python_module"
        attributes = dict(carrier.attributes)
        module = importlib.import_module(attributes["module"])
        assert hasattr(module, attributes["object"]), (
            f"{attributes['module']}:{attributes['object']} does not exist"
        )


def test_no_active_carrier_before_graduation() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    statuses = {
        str(dict(carrier.attributes).get("status"))
        for carrier in manifest.implementation
        if carrier.type == "python_module"
    }
    assert statuses == {"import_only"}
    assert "active" not in statuses
    assert activation_blocker(manifest) is not None


def test_activation_does_not_auto_attach() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.activation.entry_points == ()
    assert manifest.requires_services == ()


def test_component_contracts_are_declared_in_protocols() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    protocol_names = {protocol.name for protocol in manifest.protocols}
    for component in manifest.components:
        for contract in component.contracts:
            # "vllm.kv-transfer.events.v2" -> protocol "vllm.kv-transfer.events"
            base = ".".join(contract.split(".")[:-1])
            assert base in protocol_names, (
                f"contract {contract} has no protocol declaration"
            )
