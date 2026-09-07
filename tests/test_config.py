from pathlib import Path

import pytest

from vllm_hust_kv_transfer_observability import (
    ConfigurationError,
    EvidenceLabel,
    ObserverConfig,
)


def test_default_configuration_is_explicitly_disabled() -> None:
    config = ObserverConfig.from_mapping({})

    assert not config.enabled
    assert config.event_path is None
    assert config.descriptor_dir is None
    assert config.evidence_label is None


def test_configuration_input_must_be_a_mapping() -> None:
    with pytest.raises(ConfigurationError, match="mapping"):
        ObserverConfig.from_mapping([])  # type: ignore[arg-type]


def test_closed_configuration_parses_paths_and_evidence_label(tmp_path: Path) -> None:
    config = ObserverConfig.from_mapping(
        {
            "enabled": True,
            "event_path": str(tmp_path / "events.jsonl"),
            "descriptor_dir": str(tmp_path),
            "evidence_label": "existing-server-probe",
            "max_pending_records": 32,
            "max_record_bytes": 4096,
            "max_file_bytes": 8192,
            "max_correlated_transfers": 16,
            "max_recovery_admissions": 8,
            "max_descriptor_regions": 4,
            "max_descriptor_record_bytes": 2048,
            "shutdown_timeout_seconds": 1.5,
        }
    )

    assert config.enabled
    assert config.event_path == tmp_path / "events.jsonl"
    assert config.descriptor_dir == tmp_path
    assert config.evidence_label is EvidenceLabel.EXISTING_SERVER_PROBE


@pytest.mark.parametrize(
    "raw",
    [
        {"event_file": "events.jsonl"},
        {1: "not-a-string-key"},
        {"enabled": "true", "event_path": "events.jsonl"},
        {"enabled": True},
        {"enabled": True, "event_path": ""},
        {"enabled": True, "event_path": "bad\x00path"},
        {"descriptor_dir": "."},
        {"evidence_label": "real-online"},
        {"descriptor_dir": ".", "evidence_label": "unknown"},
    ],
)
def test_unknown_malformed_or_conflicting_configuration_fails_closed(raw) -> None:
    with pytest.raises(ConfigurationError):
        ObserverConfig.from_mapping(raw)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_pending_records", True),
        ("max_pending_records", 4097),
        ("max_record_bytes", 511),
        ("max_record_bytes", 1024 * 1024 + 1),
        ("max_file_bytes", 511),
        ("max_file_bytes", 64 * 1024 * 1024 + 1),
        ("max_correlated_transfers", 0),
        ("max_correlated_transfers", 4097),
        ("max_recovery_admissions", 4097),
        ("max_descriptor_regions", 0),
        ("max_descriptor_regions", 4097),
        ("max_descriptor_record_bytes", 0),
        ("max_descriptor_record_bytes", 1024 * 1024 + 1),
        ("shutdown_timeout_seconds", float("nan")),
        ("shutdown_timeout_seconds", 30.1),
    ],
)
def test_configured_resource_bounds_are_enforced(key: str, value: object) -> None:
    with pytest.raises(ConfigurationError):
        ObserverConfig.from_mapping({key: value})


def test_event_file_must_fit_inside_configured_file_bound() -> None:
    with pytest.raises(ConfigurationError, match="max_file_bytes"):
        ObserverConfig.from_mapping({"max_record_bytes": 4096, "max_file_bytes": 2048})


def test_direct_construction_does_not_bypass_closed_types() -> None:
    with pytest.raises(ConfigurationError, match="event_path"):
        ObserverConfig(enabled=True, event_path="events.jsonl")  # type: ignore[arg-type]
