# SPDX-License-Identifier: Apache-2.0
"""Offline validation of canonical v2 artifacts, not runtime qualification."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from .correlated import (
    CORRELATED_SCHEMA,
    CorrelatedEvent,
    CorrelatedHostObservation,
    JobReceipt,
    WorkerReceipt,
)
from .descriptors import DescriptorInventory, DescriptorRegion, EvidenceLabel
from .host_facts import HOST_FACT_SCHEMA, HOST_FACT_SCOPE, HostFact, HostFactEvent
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


def host_fact_from_payload(payload: object) -> HostFact:
    """Validate unjoined host reports separately from canonical recovery chains."""
    obj = _object(payload)
    if obj.get("schema") != HOST_FACT_SCHEMA or obj.get("scope") != HOST_FACT_SCOPE:
        raise ValueError("expected unjoined host fact schema")
    fields = dict(obj)
    del fields["schema"]
    del fields["scope"]
    try:
        fields["event"] = HostFactEvent(fields["event"])
        for name, enum_type in (
            ("compute_kind", ComputeKind),
            ("requeue_reason", RecoveryRequeueReason),
        ):
            if name in fields:
                fields[name] = enum_type(fields[name])
        for name in ("job_ids", "ranks"):
            if name in fields:
                if type(fields[name]) is not list:
                    raise ValueError(f"{name} must be an array")
                fields[name] = tuple(fields[name])
        result = HostFact(**fields)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid closed host fact record") from exc
    if result.to_payload() != obj:
        raise ValueError("noncanonical host fact fields")
    return result


def correlated_from_payload(payload: object) -> CorrelatedHostObservation:
    """Reject unknown fields and malformed nested scheduler/Worker receipts."""
    obj = _object(payload)
    if obj.get("schema") != CORRELATED_SCHEMA:
        raise ValueError("expected correlated host schema v2")
    fields = dict(obj)
    del fields["schema"]
    try:
        fields["event"] = CorrelatedEvent(fields["event"])
        if "workers" in fields:
            if type(fields["workers"]) is not list:
                raise ValueError("workers must be an array")
            fields["workers"] = tuple(
                WorkerReceipt(**_object(worker)) for worker in fields["workers"]
            )
        if "roster" in fields:
            if type(fields["roster"]) is not list:
                raise ValueError("roster must be an array")
            roster = []
            for value in fields["roster"]:
                job = _object(value)
                if (
                    set(job) != {"job_id", "workers"}
                    or type(job["workers"]) is not list
                ):
                    raise ValueError("invalid job roster")
                roster.append(
                    JobReceipt(
                        job["job_id"],
                        tuple(
                            WorkerReceipt(**_object(worker))
                            for worker in job["workers"]
                        ),
                    )
                )
            fields["roster"] = tuple(roster)
        result = CorrelatedHostObservation(**fields)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid closed correlated host record") from exc
    if result.to_payload() != obj:
        raise ValueError("noncanonical correlated host fields")
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


def read_events(
    path: Path,
) -> list[KVTransferObservation | HostFact | CorrelatedHostObservation]:
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
            payload = _decode(line)
            if _object(payload).get("schema") == HOST_FACT_SCHEMA:
                records.append(host_fact_from_payload(payload))
            elif _object(payload).get("schema") == CORRELATED_SCHEMA:
                records.append(correlated_from_payload(payload))
            else:
                records.append(observation_from_payload(payload))
    if not records:
        raise ValueError("no events recorded")
    return records


def validate_correlated_restore_chain(
    records: Iterable[KVTransferObservation | HostFact | CorrelatedHostObservation],
    request_id: str,
) -> int:
    """Prove exact Worker-incarnation rosters without comparing process clocks."""
    groups: dict[tuple[str, str, int], list[CorrelatedHostObservation]] = {}
    job_owner: dict[tuple[str, int], tuple[str, str, int]] = {}
    for record in records:
        if (
            type(record) is not CorrelatedHostObservation
            or record.request_id != request_id
        ):
            continue
        identity = (
            record.scheduler_generation,
            record.request_id,
            record.recovery_epoch,
        )
        groups.setdefault(identity, []).append(record)
        job_ids = ([record.job_id] if record.job_id is not None else []) + [
            job.job_id for job in record.roster
        ]
        for job_id in job_ids:
            owner_key = (record.scheduler_generation, job_id)
            if owner_key in job_owner and job_owner[owner_key] != identity:
                raise ValueError("job ID reused across recovery episodes")
            job_owner[owner_key] = identity
    if not groups:
        raise ValueError("requested recovery chain is absent or incomplete")

    def only(items: list[CorrelatedHostObservation], event: CorrelatedEvent):
        matching = [item for item in items if item.event is event]
        if len(matching) != 1:
            raise ValueError(f"expected one {event.value} per recovery episode")
        return matching[0]

    for items in groups.values():
        requeued = only(items, CorrelatedEvent.RECOVERY_REQUEUED)
        admitted = only(items, CorrelatedEvent.RECOVERY_ADMITTED)
        if admitted.observed_at_ns <= requeued.observed_at_ns:
            raise ValueError("scheduler admission precedes requeue")
        roster = {job.job_id: job for job in admitted.roster}
        receipts = [
            item for item in items if item.event is CorrelatedEvent.TRANSFER_RECEIPT
        ]
        if len(receipts) != len(roster) or {item.job_id for item in receipts} != set(
            roster
        ):
            raise ValueError("admission lacks the exact transfer receipts")
        for receipt in receipts:
            if (
                receipt.workers != roster[receipt.job_id].workers
                or not requeued.observed_at_ns
                < receipt.observed_at_ns
                < admitted.observed_at_ns
            ):
                raise ValueError("transfer receipt differs from admission roster")
        expected = {
            (job.job_id, worker.rank, worker.worker_generation)
            for job in admitted.roster
            for worker in job.workers
        }
        stages: dict[
            CorrelatedEvent, dict[tuple[int, int, str], CorrelatedHostObservation]
        ] = {}
        for event in (
            CorrelatedEvent.RESTORE_SUBMITTED,
            CorrelatedEvent.RESTORE_COMPLETED,
        ):
            matches = [item for item in items if item.event is event]
            stage = {
                (item.job_id, item.rank, item.worker_generation): item
                for item in matches
            }
            if len(stage) != len(matches) or set(stage) != expected:
                raise ValueError("restore stage does not match exact Worker roster")
            stages[event] = stage
        for key in expected:
            if (
                stages[CorrelatedEvent.RESTORE_COMPLETED][key].observed_at_ns
                <= stages[CorrelatedEvent.RESTORE_SUBMITTED][key].observed_at_ns
            ):
                raise ValueError("Worker completion precedes submission")
        expected_workers = {
            (worker.rank, worker.worker_generation)
            for job in admitted.roster
            for worker in job.workers
        }
        first = [item for item in items if item.event is CorrelatedEvent.FIRST_COMPUTE]
        first_by_worker = {(item.rank, item.worker_generation): item for item in first}
        if (
            len(first_by_worker) != len(first)
            or set(first_by_worker) != expected_workers
        ):
            raise ValueError("first compute does not match exact Worker roster")
        for worker, item in first_by_worker.items():
            if item.roster != admitted.roster or any(
                item.observed_at_ns
                <= stages[CorrelatedEvent.RESTORE_COMPLETED][key].observed_at_ns
                for key in expected
                if key[1:] == worker
            ):
                raise ValueError("first compute has mismatched roster or Worker order")
    return len(groups)


def validate_restore_chain(
    records: Iterable[KVTransferObservation | HostFact | CorrelatedHostObservation],
    request_id: str,
) -> int:
    """Require complete requeue/restore/admit/compute episodes for a request.

    Each worker generation/rank/epoch is checked independently. Roster and
    receipt validation reuse LifecycleNormalizer; no identity or missing
    event is synthesized. Cross-worker clock comparisons are not performed.
    """
    records = list(records)
    if any(
        type(record) is CorrelatedHostObservation and record.request_id == request_id
        for record in records
    ):
        return validate_correlated_restore_chain(records, request_id)
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
            if type(record) in {HostFact, CorrelatedHostObservation}:
                continue
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
    capture_dir: Path,
    records: Iterable[KVTransferObservation | HostFact | CorrelatedHostObservation],
) -> int:
    """Check schema and explicit transfer/worker correlation, never filenames."""
    transfers: dict[
        TransferIdentity, tuple[ObservationIdentity, TransferDirection]
    ] = {}
    for event in records:
        if type(event) in {HostFact, CorrelatedHostObservation}:
            continue
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
    parser.add_argument("--events", type=Path, required=True, action="append")
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--expect-restore-chain")
    args = parser.parse_args(argv)
    try:
        if sum(path.stat().st_size for path in args.events) > MAX_ARTIFACT_BYTES:
            raise ValueError("combined event artifacts exceed the byte limit")
        records = [record for path in args.events for record in read_events(path)]
        if len(records) > MAX_RECORDS:
            raise ValueError("combined event artifacts exceed the record limit")
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
                "unjoined_host_facts": sum(type(r) is HostFact for r in records),
                "correlated_host_records": sum(
                    type(r) is CorrelatedHostObservation for r in records
                ),
                "complete_restore_episodes": chains,
                "descriptor_inventories": descriptors,
                "scope": "offline checks only; no hardware or host qualification",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
