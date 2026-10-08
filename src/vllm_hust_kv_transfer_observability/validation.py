# SPDX-License-Identifier: Apache-2.0
"""Offline validation of canonical v2 artifacts, not runtime qualification."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from .descriptors import DescriptorInventory, DescriptorRegion, EvidenceLabel
from .normalization import (
    CoreRecoveryAdmitted,
    CoreRecoveryRequeued,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    FirstComputeObserved,
    LifecycleNormalizer,
    SourceHost,
    TransferOperation,
)
from .schema import (
    ComputeKind,
    KVTransferObservation,
    ObservationEvent,
    ObservationIdentity,
    ReceiptIdentity,
    RecoveryRequeueReason,
    TransferDirection,
    TransferIdentity,
    TransferTerminalReason,
)

MAX_RECORD_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000


def _object(value: object) -> dict:
    if type(value) is not dict:
        raise ValueError("expected a JSON object")
    return value


def _identity(value: object) -> ObservationIdentity:
    obj = _object(value)
    if set(obj) != {"request_id", "worker_generation", "rank", "recovery_epoch"}:
        raise ValueError("identity fields do not match v2")
    return ObservationIdentity(**obj)


def observation_from_payload(payload: object) -> KVTransferObservation:
    """Reconstruct using the writer's typed schema; reject extra/null fields."""
    obj = _object(payload)
    if obj.get("schema") != "vllm-hust.kv-transfer-event.v2":
        raise ValueError("expected canonical event schema v2")
    fields = dict(obj)
    del fields["schema"]
    try:
        fields["identity"] = _identity(fields["identity"])
        fields["event"] = ObservationEvent(fields["event"])
        # A missing timestamp must not be replaced with the constructor's clock.
        fields["observed_at_ns"] = obj["observed_at_ns"]
        for name, constructor in (
            ("direction", TransferDirection),
            ("compute_kind", ComputeKind),
            ("requeue_reason", RecoveryRequeueReason),
            ("terminal_reason", TransferTerminalReason),
        ):
            if name in fields:
                fields[name] = constructor(fields[name])
        for source, target, constructor in (
            ("transfer_id", "transfer", TransferIdentity),
            ("receipt_id", "receipt", ReceiptIdentity),
        ):
            if source in fields:
                if target in fields:
                    raise ValueError("noncanonical event fields")
                fields[target] = constructor(fields.pop(source))
        if "associated_transfer_ids" in fields:
            if "associated_transfers" in fields:
                raise ValueError("noncanonical association fields")
            values = fields.pop("associated_transfer_ids")
            if type(values) is not list:
                raise ValueError("associated_transfer_ids must be a JSON array")
            fields["associated_transfers"] = tuple(TransferIdentity(v) for v in values)
        result = KVTransferObservation(**fields)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid closed event record") from exc
    if result.to_payload() != obj:
        raise ValueError("noncanonical event fields")
    return result


def descriptor_from_payload(payload: object) -> DescriptorInventory:
    """Validate every v2 region through the same types used by capture."""
    obj = _object(payload)
    expected = {
        "schema",
        "identity",
        "transfer_id",
        "job_id",
        "direction",
        "descriptors",
        "observed_at_ns",
        "evidence_label",
    }
    if set(obj) != expected:
        raise ValueError("descriptor inventory fields do not match v2")
    if obj["schema"] != "vllm-hust.kv-transfer-descriptor-layout.v2":
        raise ValueError("expected descriptor schema v2")
    try:
        if type(obj["descriptors"]) is not list:
            raise ValueError("descriptors must be an array")
        regions = []
        for value in obj["descriptors"]:
            fields = dict(_object(value))
            fields["direction"] = TransferDirection(fields["direction"])
            regions.append(DescriptorRegion(**fields))
        result = DescriptorInventory(
            identity=_identity(obj["identity"]),
            transfer=TransferIdentity(obj["transfer_id"]),
            job_id=obj["job_id"],
            direction=TransferDirection(obj["direction"]),
            regions=tuple(regions),
            observed_at_ns=obj["observed_at_ns"],
        )
        label = EvidenceLabel(obj["evidence_label"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid closed descriptor inventory") from exc
    if result.to_payload(label) != obj:
        raise ValueError("noncanonical descriptor fields")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON key")
        obj[key] = value
    return obj


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _decode(data: bytes) -> object:
    return json.loads(
        data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )


def read_events(path: Path) -> list[KVTransferObservation]:
    """Read a bounded JSONL artifact without accepting ambiguous JSON keys."""
    records = []
    total = 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            total += len(line)
            if len(line) > MAX_RECORD_BYTES or total > MAX_ARTIFACT_BYTES:
                raise ValueError("event artifact exceeds the byte limit")
            if not line.strip():
                continue
            if len(records) >= MAX_RECORDS:
                raise ValueError("event artifact exceeds the record limit")
            records.append(observation_from_payload(_decode(line)))
    if not records:
        raise ValueError("no events recorded")
    return records


def validate_restore_chain(
    records: Iterable[KVTransferObservation], request_id: str
) -> int:
    """Require complete requeue/restore/admit/compute episodes for a request.

    Each worker generation/rank/epoch is checked independently. Roster and
    receipt validation reuse LifecycleNormalizer; no identity or missing
    event is synthesized. Cross-worker clock comparisons are not performed.
    """
    events = ObservationEvent
    relevant = {
        events.RECOVERY_REQUEUED,
        events.RESTORE_STARTED,
        events.RESTORE_COMPLETED,
        events.RECOVERY_ADMITTED,
        events.FIRST_COMPUTE,
    }
    episodes: dict[ObservationIdentity, dict] = {}
    seen_transfers: set[TransferIdentity] = set()
    seen_receipts: set[ReceiptIdentity] = set()
    normalizer = LifecycleNormalizer()
    try:
        for record in records:
            if record.identity.request_id != request_id:
                continue
            event, identity = record.event, record.identity
            if event not in relevant:
                if record.direction is TransferDirection.H2D:
                    raise ValueError("requested recovery contains a non-success path")
                continue
            if event is events.RECOVERY_REQUEUED:
                if identity in episodes:
                    raise ValueError("duplicate recovery episode")
                episodes[identity] = {
                    "started": set(),
                    "completed": set(),
                    "admitted": False,
                    "computed": False,
                    "last_ns": record.observed_at_ns,
                }
            episode = episodes.get(identity)
            if episode is None or episode["computed"]:
                raise ValueError("missing requeue or event after first compute")
            if record.observed_at_ns < episode["last_ns"]:
                raise ValueError("episode events are out of order")
            episode["last_ns"] = record.observed_at_ns
            if record.receipt is not None:
                if record.receipt in seen_receipts:
                    raise ValueError("receipt identity reused")
                seen_receipts.add(record.receipt)
            if event is events.RECOVERY_REQUEUED:
                source = CoreRecoveryRequeued(
                    identity, record.requeue_reason, record.observed_at_ns
                )
            elif event is events.RESTORE_STARTED:
                if episode["admitted"] or record.transfer in seen_transfers:
                    raise ValueError("late or duplicate restore submission")
                seen_transfers.add(record.transfer)
                episode["started"].add(record.transfer)
                source = CoreTransferSubmitted(
                    identity,
                    record.transfer,
                    TransferOperation.H2D_RESTORE,
                    record.block_count,
                    record.observed_at_ns,
                )
            elif event is events.RESTORE_COMPLETED:
                if episode["admitted"]:
                    raise ValueError("completion after admission")
                episode["completed"].add(record.transfer)
                source = CoreTransferCompleted(
                    record.transfer,
                    record.observed_at_ns,
                    True,
                    record.bytes_moved,
                    record.device_duration_ns,
                    record.receipt,
                )
            elif event is events.RECOVERY_ADMITTED:
                roster = set(record.associated_transfers)
                if (
                    episode["admitted"]
                    or not roster
                    or roster != episode["started"]
                    or roster != episode["completed"]
                ):
                    raise ValueError("admission does not contain the complete roster")
                episode["admitted"] = True
                source = CoreRecoveryAdmitted(
                    identity, record.associated_transfers, record.observed_at_ns
                )
            else:
                source = FirstComputeObserved(
                    SourceHost.VLLM,
                    identity,
                    record.receipt,
                    record.associated_transfers,
                    record.compute_kind,
                    record.observed_at_ns,
                )
                episode["computed"] = True
            # Canonical records omit the source host; VLLM above is only the
            # normalizer's neutral projection, not attribution of this run.
            if normalizer.normalize(source) != record:
                raise ValueError(
                    "lifecycle identity, receipt, timing or order mismatch"
                )
        if not episodes or any(not e["computed"] for e in episodes.values()):
            raise ValueError("requested recovery chain is absent or incomplete")
        return len(episodes)
    finally:
        normalizer.close()


def validate_descriptors(
    capture_dir: Path, records: Iterable[KVTransferObservation]
) -> int:
    """Check schema and explicit transfer/worker correlation, never filenames."""
    transfers: dict[
        TransferIdentity, tuple[ObservationIdentity, TransferDirection]
    ] = {}
    for event in records:
        if event.transfer is None:
            continue
        key = (event.identity, event.direction)
        if event.transfer in transfers and transfers[event.transfer] != key:
            raise ValueError("transfer identity has inconsistent event ownership")
        transfers[event.transfer] = key
    count = total = 0
    seen: set[TransferIdentity] = set()
    if not capture_dir.is_dir():
        raise ValueError("descriptor directory missing")
    for path in capture_dir.glob("*.json"):
        with path.open("rb") as stream:
            data = stream.read(MAX_RECORD_BYTES + 1)
        total += len(data)
        count += 1
        if (
            len(data) > MAX_RECORD_BYTES
            or total > MAX_ARTIFACT_BYTES
            or count > MAX_RECORDS
        ):
            raise ValueError("descriptor artifacts exceed the validation limit")
        inventory = descriptor_from_payload(_decode(data))
        if inventory.transfer in seen:
            raise ValueError("duplicate descriptor inventory")
        seen.add(inventory.transfer)
        if transfers.get(inventory.transfer) != (
            inventory.identity,
            inventory.direction,
        ):
            raise ValueError("descriptor has no matching event identity/direction")
    if count == 0:
        raise ValueError("no descriptor inventories recorded")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--expect-restore-chain")
    args = parser.parse_args(argv)
    try:
        records = read_events(args.events)
        chains = (
            validate_restore_chain(records, args.expect_restore_chain)
            if args.expect_restore_chain is not None
            else None
        )
        descriptors = (
            validate_descriptors(args.capture_dir, records)
            if args.capture_dir is not None
            else None
        )
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        # Avoid echoing untrusted record contents or payloads into logs.
        print(
            f"FAIL: artifact validation rejected ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "events": len(records),
                "complete_restore_episodes": chains,
                "descriptor_inventories": descriptors,
                "scope": "offline checks only; no hardware or host qualification",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
