import copy
import json
import subprocess
import sys

import pytest

from vllm_hust_kv_transfer_observability import (
    ComputeKind,
    CoreRecoveryAdmitted,
    CoreRecoveryRequeued,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    FirstComputeObserved,
    LifecycleNormalizer,
    ObservationIdentity,
    ReceiptIdentity,
    RecoveryRequeueReason,
    SourceHost,
    TransferIdentity,
    TransferOperation,
)
from vllm_hust_kv_transfer_observability.descriptors import (
    DescriptorInventory,
    DescriptorRegion,
    EvidenceLabel,
)
from vllm_hust_kv_transfer_observability.host_facts import HostFact, HostFactEvent
from vllm_hust_kv_transfer_observability.schema import TransferDirection
from vllm_hust_kv_transfer_observability.validation import (
    descriptor_from_payload,
    host_fact_from_payload,
    main,
    observation_from_payload,
    read_events,
    validate_descriptors,
    validate_restore_chain,
)


def chain(*, rank=0, generation=2, epoch=1, process="a", transfers=1):
    """Synthetic conformance only; generated through the actual normalizer."""
    identity = ObservationIdentity("req-1", generation, rank, epoch)
    roster = tuple(TransferIdentity(f"{process * 32}:t:{i}") for i in range(transfers))
    sources = [CoreRecoveryRequeued(identity, RecoveryRequeueReason.UNCLASSIFIED, 1)]
    for i, transfer in enumerate(roster):
        sources.extend(
            [
                CoreTransferSubmitted(
                    identity, transfer, TransferOperation.H2D_RESTORE, 4, 10 + 20 * i
                ),
                CoreTransferCompleted(
                    transfer,
                    20 + 20 * i,
                    True,
                    1024,
                    5,
                    ReceiptIdentity(f"{process * 32}:k:{i}"),
                ),
            ]
        )
    sources.extend(
        [
            CoreRecoveryAdmitted(identity, roster, 100),
            FirstComputeObserved(
                SourceHost.VLLM,
                identity,
                ReceiptIdentity(f"{process * 32}:k:99"),
                roster,
                ComputeKind.DECODE,
                110,
            ),
        ]
    )
    normalizer = LifecycleNormalizer()
    records = [normalizer.normalize(source) for source in sources]
    assert all(record is not None for record in records)
    normalizer.close()
    return records


def inventory(record=None):
    record = record or chain()[1]
    return DescriptorInventory(
        identity=record.identity,
        transfer=record.transfer,
        job_id=8,
        direction=TransferDirection.H2D,
        regions=(DescriptorRegion(0, 64, 1024, TransferDirection.H2D, 3, 7),),
        observed_at_ns=9,
    )


def write_events(path, records):
    path.write_text("".join(json.dumps(r.to_payload()) + "\n" for r in records))
    return path


def test_current_writer_roundtrips_and_complete_chain(tmp_path):
    records = chain(transfers=2)
    events = write_events(tmp_path / "events.jsonl", records)
    assert read_events(events) == records
    assert validate_restore_chain(records, "req-1") == 1
    value = inventory().to_payload(EvidenceLabel.REPLAY)
    assert descriptor_from_payload(value) == inventory()
    capture = tmp_path / "captures"
    capture.mkdir()
    (capture / "unrelated-name.json").write_text(json.dumps(value))
    assert validate_descriptors(capture, records) == 1
    assert (
        main(
            [
                "--events",
                str(events),
                "--capture-dir",
                str(capture),
                "--expect-restore-chain",
                "req-1",
            ]
        )
        == 0
    )


def test_unjoined_host_fact_roundtrips_but_cannot_complete_chain(tmp_path, capsys):
    fact = HostFact(
        HostFactEvent.RECOVERY_ADMITTED,
        "f" * 32,
        42,
        "req-1",
        recovery_epoch=1,
        job_ids=(7, 8),
    )
    assert host_fact_from_payload(fact.to_payload()) == fact
    path = tmp_path / "mixed.jsonl"
    path.write_text(json.dumps(fact.to_payload()) + "\n")
    assert read_events(path) == [fact]
    assert main(["--events", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["unjoined_host_facts"] == 1
    with pytest.raises(ValueError, match="absent or incomplete"):
        validate_restore_chain(read_events(path), "req-1")
    write_events(path, chain() + [fact])
    assert validate_restore_chain(read_events(path), "req-1") == 1


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "correlated"},
        {"request_id": "bad\x00id"},
        {"job_ids": [8, 7]},
        {"job_ids": [7, 7]},
        {"job_ids": [True]},
        {"unexpected": "0xdeadbeef"},
    ],
)
def test_host_fact_validator_rejects_noncanonical_or_unsafe_fields(change):
    fact = HostFact(
        HostFactEvent.RECOVERY_ADMITTED,
        "f" * 32,
        42,
        "req-1",
        recovery_epoch=1,
        job_ids=(7,),
    )
    payload = fact.to_payload()
    payload.update(change)
    with pytest.raises(ValueError):
        host_fact_from_payload(payload)


def test_independent_worker_clocks_and_generations():
    first = chain()
    second = chain(rank=1, process="b")
    third = chain(generation=3, process="c")
    assert validate_restore_chain(first + second + third, "req-1") == 3


@pytest.mark.parametrize("index", range(5))
def test_every_missing_chain_stage_is_rejected(index):
    records = chain()
    del records[index]
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


@pytest.mark.parametrize("index", range(5))
def test_every_duplicate_chain_stage_is_rejected(index):
    records = chain()
    records.insert(index, records[index])
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


@pytest.mark.parametrize("index", range(4))
def test_reordered_stages_rejected(index):
    records = chain()
    records[index], records[index + 1] = records[index + 1], records[index]
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_generation", 3),
        ("rank", 4),
        ("recovery_epoch", 2),
        ("request_id", "another-request"),
    ],
)
def test_completed_restore_cannot_change_identity(field, value):
    records = chain()
    payload = records[2].to_payload()
    payload["identity"][field] = value
    records[2] = observation_from_payload(payload)
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


def test_partial_admission_roster_cannot_hide_a_completed_restore():
    records = chain(transfers=2)
    for index in (-2, -1):
        payload = records[index].to_payload()
        payload["associated_transfer_ids"] = payload["associated_transfer_ids"][:1]
        records[index] = observation_from_payload(payload)
    with pytest.raises(ValueError, match="complete roster"):
        validate_restore_chain(records, "req-1")


def test_first_compute_must_have_its_own_receipt():
    records = chain()
    payload = records[-1].to_payload()
    payload["receipt_id"] = records[2].receipt.value
    records[-1] = observation_from_payload(payload)
    with pytest.raises(ValueError, match="reused"):
        validate_restore_chain(records, "req-1")


@pytest.mark.parametrize("field,value", [("block_count", 7), ("duration_ns", 99)])
def test_completion_measurement_must_match_submission(field, value):
    records = chain()
    payload = records[2].to_payload()
    payload[field] = value
    records[2] = observation_from_payload(payload)
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


def test_first_compute_must_match_admission_roster():
    records = chain(transfers=2)
    payload = records[-1].to_payload()
    payload["associated_transfer_ids"] = payload["associated_transfer_ids"][:1]
    records[-1] = observation_from_payload(payload)
    with pytest.raises(ValueError):
        validate_restore_chain(records, "req-1")


def test_absent_requested_chain_fails_even_with_valid_event():
    with pytest.raises(ValueError):
        validate_restore_chain(chain(), "absent")
    with pytest.raises(ValueError):
        validate_restore_chain(chain()[1:2], "req-1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", 1234),
        ("payload", [1, 2]),
        ("bytes_moved", None),
        ("observed_at_ns", True),
        ("observed_at_ns", -1),
        ("schema", "vllm-hust.kv-transfer-event.v1"),
        ("associated_transfer_ids", []),
    ],
)
def test_closed_event_validation_is_unconditional(field, value):
    payload = chain()[1].to_payload()
    payload[field] = value
    with pytest.raises(ValueError):
        observation_from_payload(payload)


@pytest.mark.parametrize("field", ["observed_at_ns", "identity", "event"])
def test_required_event_fields_are_not_synthesized(field):
    payload = chain()[1].to_payload()
    del payload[field]
    with pytest.raises(ValueError):
        observation_from_payload(payload)


@pytest.mark.parametrize(
    "field,value", [("rank", True), ("address", 1), ("worker_generation", None)]
)
def test_identity_closed_schema(field, value):
    payload = chain()[1].to_payload()
    payload["identity"][field] = value
    with pytest.raises(ValueError):
        observation_from_payload(payload)


@pytest.mark.parametrize(
    "text",
    [
        '{"schema":1,"schema":2}',
        '{"identity":{"rank":0,"rank":1}}',
        '{"address":NaN}',
        "[]",
        "",
        "\n",
        "{invalid",
    ],
)
def test_malformed_ambiguous_or_empty_json_rejected(tmp_path, text):
    path = tmp_path / "bad.jsonl"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_events(path)
    assert main(["--events", str(path)]) == 1


def test_size_limits_fail_closed(tmp_path, monkeypatch):
    from vllm_hust_kv_transfer_observability import validation

    path = write_events(tmp_path / "events.jsonl", chain())
    monkeypatch.setattr(validation, "MAX_RECORD_BYTES", 20)
    with pytest.raises(ValueError, match="byte limit"):
        read_events(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("src_region_id", True),
        ("dst_region_id", -1),
        ("size", 0),
        ("size", True),
        ("address", 123),
        ("direction", "d2h"),
    ],
)
def test_descriptor_regions_are_closed_and_typed(field, value):
    payload = inventory().to_payload(EvidenceLabel.REPLAY)
    payload["descriptors"][0][field] = value
    with pytest.raises(ValueError):
        descriptor_from_payload(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("descriptors", []),
        ("job_id", True),
        ("payload", "forbidden"),
        ("schema", "vllm-hust.kv-transfer-descriptor-layout.v1"),
    ],
)
def test_descriptor_inventory_rejects_legacy_or_invalid(field, value):
    payload = inventory().to_payload(EvidenceLabel.REPLAY)
    payload[field] = value
    with pytest.raises(ValueError):
        descriptor_from_payload(payload)


def test_descriptor_must_match_event_identity_and_be_unique(tmp_path):
    payload = inventory().to_payload(EvidenceLabel.REPLAY)
    path = tmp_path / "capture.json"
    bad = copy.deepcopy(payload)
    bad["identity"]["rank"] = 1
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="matching event"):
        validate_descriptors(tmp_path, chain())
    path.write_text(json.dumps(payload))
    (tmp_path / "duplicate.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        validate_descriptors(tmp_path, chain())


def test_cli_rejects_payload_even_without_chain_flag(tmp_path):
    path = tmp_path / "events.jsonl"
    payload = chain()[1].to_payload()
    payload["payload"] = "PRIVATE SENTINEL"
    path.write_text(json.dumps(payload))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vllm_hust_kv_transfer_observability.validation",
            "--events",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "PRIVATE SENTINEL" not in result.stdout + result.stderr


def test_empty_capture_directory_rejected(tmp_path):
    with pytest.raises(ValueError, match="no descriptor"):
        validate_descriptors(tmp_path, chain())
