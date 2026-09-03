import threading
from dataclasses import FrozenInstanceError

import pytest

from vllm_hust_kv_transfer_observability import (
    ComputeKind,
    CoreRecoveryAdmitted,
    CoreRecoveryRequeued,
    CoreTransferCancelled,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    FirstComputeObserved,
    LifecycleNormalizer,
    ObservationEvent,
    ObservationIdentity,
    ReceiptIdentity,
    RecoveryRequeueReason,
    SourceHost,
    TransferIdentity,
    TransferOperation,
    TransferTerminalReason,
)

PROCESS_UUID = "d" * 32


def identity(
    request_id: str = "request-1",
    *,
    generation: int = 2,
    epoch: int | None = 1,
) -> ObservationIdentity:
    return ObservationIdentity(request_id, generation, 0, recovery_epoch=epoch)


def transfer(sequence: int = 1) -> TransferIdentity:
    return TransferIdentity(f"{PROCESS_UUID}:t:{sequence}")


def receipt(sequence: int = 1) -> ReceiptIdentity:
    return ReceiptIdentity(f"{PROCESS_UUID}:k:{sequence}")


def complete_restore(
    normalizer: LifecycleNormalizer,
    *,
    request_identity: ObservationIdentity | None = None,
    transfer_identity: TransferIdentity | None = None,
) -> list:
    resolved_identity = request_identity or identity()
    resolved_transfer = transfer_identity or transfer()
    start = normalizer.normalize(
        CoreTransferSubmitted(
            resolved_identity,
            resolved_transfer,
            TransferOperation.H2D_RESTORE,
            block_count=4,
            observed_at_ns=10,
        )
    )
    done = normalizer.normalize(
        CoreTransferCompleted(
            resolved_transfer,
            observed_at_ns=30,
            success=True,
            bytes_moved=4096,
            device_duration_ns=7,
            receipt=receipt(),
        )
    )
    return [start, done]


@pytest.mark.parametrize("source_host", list(SourceHost))
def test_core_restore_and_host_first_compute_share_one_model(
    source_host: SourceHost,
) -> None:
    normalizer = LifecycleNormalizer()
    events = complete_restore(normalizer)
    events.append(
        normalizer.normalize(
            CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=40)
        )
    )
    events.append(
        normalizer.normalize(
            FirstComputeObserved(
                source_host,
                identity(),
                receipt(2),
                (transfer(),),
                ComputeKind.DECODE,
                observed_at_ns=50,
            )
        )
    )

    assert [event.event for event in events if event is not None] == [
        ObservationEvent.RESTORE_STARTED,
        ObservationEvent.RESTORE_COMPLETED,
        ObservationEvent.RECOVERY_ADMITTED,
        ObservationEvent.FIRST_COMPUTE,
    ]
    assert events[1] is not None
    assert events[1].duration_ns == 20
    assert events[1].device_duration_ns == 7
    assert events[-1] is not None
    assert "source" not in events[-1].to_payload()
    assert normalizer.evidence_valid
    assert normalizer.counters.accepted == 4


def test_d2h_preserve_keeps_phase_and_transfer_identity() -> None:
    normalizer = LifecycleNormalizer()
    request_identity = identity(epoch=None)
    started = normalizer.normalize(
        CoreTransferSubmitted(
            request_identity,
            transfer(),
            TransferOperation.D2H_PRESERVE,
            block_count=3,
            observed_at_ns=10,
        )
    )
    completed = normalizer.normalize(
        CoreTransferCompleted(
            transfer(),
            observed_at_ns=15,
            success=True,
            bytes_moved=1024,
        )
    )

    assert started is not None and started.event is ObservationEvent.PRESERVE_STARTED
    assert completed is not None
    assert completed.event is ObservationEvent.PRESERVE_COMPLETED
    assert completed.identity == request_identity
    assert completed.transfer == transfer()
    assert completed.duration_ns == 5


def test_failure_and_explicit_invalidation_remain_distinct() -> None:
    normalizer = LifecycleNormalizer()
    for sequence in (1, 2):
        assert normalizer.normalize(
            CoreTransferSubmitted(
                identity(),
                transfer(sequence),
                TransferOperation.H2D_RESTORE,
                block_count=1,
                observed_at_ns=sequence,
            )
        )

    failed = normalizer.normalize(CoreTransferCompleted(transfer(1), 4, success=False))
    cancelled = normalizer.normalize(
        CoreTransferCancelled(
            transfer(2),
            5,
            TransferTerminalReason.WORKER_GENERATION_CHANGED,
        )
    )

    assert failed is not None and failed.event is ObservationEvent.TRANSFER_FAILED
    assert cancelled is not None
    assert cancelled.event is ObservationEvent.TRANSFER_CANCELLED
    assert cancelled.terminal_reason is TransferTerminalReason.WORKER_GENERATION_CHANGED


def test_unknown_or_out_of_order_completion_is_dropped_without_raising() -> None:
    normalizer = LifecycleNormalizer()
    assert (
        normalizer.normalize(CoreTransferCompleted(transfer(), 10, success=False))
        is None
    )
    assert (
        normalizer.normalize(
            CoreTransferSubmitted(
                identity(),
                transfer(),
                TransferOperation.H2D_RESTORE,
                block_count=1,
                observed_at_ns=20,
            )
        )
        is not None
    )
    assert (
        normalizer.normalize(CoreTransferCompleted(transfer(), 19, success=False))
        is None
    )
    assert normalizer.counters.invalid == 2
    assert normalizer.evidence_valid


def test_admission_requires_exact_completed_restore_identity() -> None:
    normalizer = LifecycleNormalizer()
    complete_restore(normalizer)
    foreign = identity("request-2")
    assert (
        normalizer.normalize(
            CoreRecoveryAdmitted(foreign, (transfer(),), observed_at_ns=40)
        )
        is None
    )
    assert normalizer.counters.invalid == 1
    assert normalizer.evidence_valid


def test_worker_generation_change_rejects_stale_restore_receipt() -> None:
    normalizer = LifecycleNormalizer()
    complete_restore(normalizer, request_identity=identity(generation=2))
    next_generation = identity(generation=3)
    assert (
        normalizer.normalize(
            CoreRecoveryAdmitted(
                next_generation,
                (transfer(),),
                observed_at_ns=40,
            )
        )
        is None
    )
    assert normalizer.counters.invalid == 1
    assert normalizer.evidence_valid


def test_admission_and_first_compute_preserve_temporal_order() -> None:
    normalizer = LifecycleNormalizer()
    complete_restore(normalizer)
    assert (
        normalizer.normalize(
            CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=29)
        )
        is None
    )
    assert (
        normalizer.normalize(
            CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=40)
        )
        is not None
    )
    assert (
        normalizer.normalize(
            FirstComputeObserved(
                SourceHost.VLLM,
                identity(),
                receipt(2),
                (transfer(),),
                ComputeKind.DECODE,
                observed_at_ns=39,
            )
        )
        is None
    )
    assert not normalizer.evidence_valid


def test_first_compute_roster_mismatch_disables_formal_evidence() -> None:
    normalizer = LifecycleNormalizer()
    complete_restore(normalizer)
    assert (
        normalizer.normalize(
            CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=40)
        )
        is not None
    )
    assert (
        normalizer.normalize(
            FirstComputeObserved(
                SourceHost.VLLM_ASCEND,
                identity(),
                receipt(2),
                (transfer(2),),
                ComputeKind.DECODE,
                observed_at_ns=50,
            )
        )
        is None
    )
    assert not normalizer.evidence_valid
    assert (
        normalizer.normalize(
            CoreRecoveryRequeued(
                identity(), RecoveryRequeueReason.BLOCK_CAPACITY, observed_at_ns=60
            )
        )
        is None
    )
    assert normalizer.counters.invalid == 1
    assert normalizer.counters.evidence_disabled_dropped == 1


def test_duplicate_source_events_are_rejected_without_replacing_state() -> None:
    normalizer = LifecycleNormalizer()
    submitted = CoreTransferSubmitted(
        identity(),
        transfer(),
        TransferOperation.H2D_RESTORE,
        block_count=1,
        observed_at_ns=10,
    )
    assert normalizer.normalize(submitted) is not None
    assert normalizer.normalize(submitted) is None
    completed = CoreTransferCompleted(
        transfer(),
        observed_at_ns=20,
        success=True,
        bytes_moved=128,
        receipt=receipt(),
    )
    assert normalizer.normalize(completed) is not None
    assert normalizer.normalize(completed) is None

    admitted = CoreRecoveryAdmitted(identity(), (transfer(),), observed_at_ns=30)
    assert normalizer.normalize(admitted) is not None
    assert normalizer.normalize(admitted) is None

    first_compute = FirstComputeObserved(
        SourceHost.VLLM,
        identity(),
        receipt(2),
        (transfer(),),
        ComputeKind.DECODE,
        observed_at_ns=40,
    )
    assert normalizer.normalize(first_compute) is not None
    assert normalizer.normalize(first_compute) is None
    assert normalizer.counters.invalid == 4
    assert not normalizer.evidence_valid


def test_missing_source_events_are_explicitly_rejected() -> None:
    missing_submit = LifecycleNormalizer()
    assert (
        missing_submit.normalize(
            CoreTransferCompleted(transfer(), observed_at_ns=20, success=False)
        )
        is None
    )

    missing_receipt = LifecycleNormalizer()
    assert (
        missing_receipt.normalize(
            CoreTransferSubmitted(
                identity(),
                transfer(),
                TransferOperation.H2D_RESTORE,
                block_count=1,
                observed_at_ns=10,
            )
        )
        is not None
    )
    assert (
        missing_receipt.normalize(
            CoreTransferCompleted(
                transfer(),
                observed_at_ns=20,
                success=True,
                bytes_moved=128,
            )
        )
        is None
    )

    missing_admission = LifecycleNormalizer()
    assert (
        missing_admission.normalize(
            FirstComputeObserved(
                SourceHost.VLLM_ASCEND,
                identity(),
                receipt(2),
                (transfer(),),
                ComputeKind.DECODE,
                observed_at_ns=40,
            )
        )
        is None
    )
    assert not missing_admission.evidence_valid


def test_source_rosters_reject_more_than_4096_transfer_ids() -> None:
    transfers = tuple(sorted(transfer(sequence) for sequence in range(1, 4098)))
    with pytest.raises(ValueError, match="bounded association"):
        CoreRecoveryAdmitted(identity(), transfers, observed_at_ns=30)
    with pytest.raises(ValueError, match="bounded association"):
        FirstComputeObserved(
            SourceHost.VLLM_ASCEND,
            identity(),
            receipt(2),
            transfers,
            ComputeKind.DECODE,
            observed_at_ns=40,
        )


def test_capacity_is_bounded_and_disables_incomplete_evidence() -> None:
    normalizer = LifecycleNormalizer(max_correlated_transfers=1)
    assert (
        normalizer.normalize(
            CoreTransferSubmitted(
                identity(),
                transfer(),
                TransferOperation.H2D_RESTORE,
                block_count=1,
                observed_at_ns=1,
            )
        )
        is not None
    )
    assert (
        normalizer.normalize(
            CoreTransferSubmitted(
                identity(),
                transfer(2),
                TransferOperation.H2D_RESTORE,
                block_count=1,
                observed_at_ns=2,
            )
        )
        is None
    )
    assert not normalizer.evidence_valid
    assert normalizer.counters.capacity_dropped == 1

    with pytest.raises(ValueError, match="1..4096"):
        LifecycleNormalizer(max_correlated_transfers=4097)


def test_requeue_preserves_closed_reason_and_epoch() -> None:
    normalizer = LifecycleNormalizer()
    event = normalizer.normalize(
        CoreRecoveryRequeued(
            identity(), RecoveryRequeueReason.TOKEN_BUDGET, observed_at_ns=60
        )
    )
    assert event is not None
    assert event.event is ObservationEvent.RECOVERY_REQUEUED
    assert event.requeue_reason is RecoveryRequeueReason.TOKEN_BUDGET
    assert event.identity.recovery_epoch == 1


def test_untyped_input_and_close_are_fail_open() -> None:
    normalizer = LifecycleNormalizer()
    assert normalizer.normalize(object()) is None  # type: ignore[arg-type]
    normalizer.close()
    normalizer.close()
    assert (
        normalizer.normalize(
            CoreRecoveryRequeued(
                identity(), RecoveryRequeueReason.UNCLASSIFIED, observed_at_ns=1
            )
        )
        is None
    )
    assert normalizer.counters.invalid == 1
    assert normalizer.counters.closed_dropped == 1


def test_source_records_are_immutable_and_reject_ambiguous_values() -> None:
    submitted = CoreTransferSubmitted(
        identity(),
        transfer(),
        TransferOperation.H2D_RESTORE,
        block_count=1,
        observed_at_ns=1,
    )
    with pytest.raises(FrozenInstanceError):
        submitted.block_count = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="success"):
        CoreTransferCompleted(transfer(), 2, success=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cancellation"):
        CoreTransferCancelled(transfer(), 2, TransferTerminalReason.BACKEND_FAILURE)


def test_concurrent_source_delivery_keeps_correlation_consistent() -> None:
    normalizer = LifecycleNormalizer(max_correlated_transfers=64)
    results = []

    def submit(sequence: int) -> None:
        results.append(
            normalizer.normalize(
                CoreTransferSubmitted(
                    identity(f"request-{sequence}", epoch=None),
                    transfer(sequence),
                    TransferOperation.D2H_PRESERVE,
                    block_count=1,
                    observed_at_ns=sequence,
                )
            )
        )

    threads = [
        threading.Thread(target=submit, args=(sequence,)) for sequence in range(1, 33)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 32
    assert all(result is not None for result in results)
    assert normalizer.counters.accepted == 32
    assert normalizer.counters.invalid == 0
    assert normalizer.evidence_valid
