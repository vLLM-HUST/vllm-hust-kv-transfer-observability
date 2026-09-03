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
    assert manifest.activation.environment == ()


def test_component_contracts_are_declared_in_protocols() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    protocol_names = {protocol.name for protocol in manifest.protocols}
    for component in manifest.components:
        for contract in component.contracts:
            # "vllm.kv-transfer.events.v1" -> protocol "vllm.kv-transfer.events"
            base = ".".join(contract.split(".")[:-1])
            assert base in protocol_names, (
                f"contract {contract} has no protocol declaration"
            )


def test_host_version_range_is_wide_enough() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    host_spec = manifest.host
    assert host_spec.provider == "vllm"
    # The host API version supported by this extension-manager release.
    assert host_spec.version_range in (">=0", ">=0.0.0", "*")
