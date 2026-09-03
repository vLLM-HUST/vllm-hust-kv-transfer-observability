from dataclasses import FrozenInstanceError

import pytest

from vllm_hust_kv_transfer_observability import (
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

PROCESS_UUID = "a" * 32


def identity(*, recovery: bool = True) -> ObservationIdentity:
    return ObservationIdentity(
        request_id="request-1",
        worker_generation=3,
        rank=0,
        recovery_epoch=2 if recovery else None,
    )


def transfer(sequence: int = 1) -> TransferIdentity:
    return TransferIdentity(f"{PROCESS_UUID}:t:{sequence}")


def receipt(sequence: int = 1) -> ReceiptIdentity:
    return ReceiptIdentity(f"{PROCESS_UUID}:k:{sequence}")


def test_closed_event_vocabulary_has_valid_field_shapes() -> None:
    common = {"identity": identity(), "observed_at_ns": 10}
    records = [
        KVTransferObservation(
            ObservationEvent.PRESERVE_STARTED,
            transfer=transfer(),
            direction=TransferDirection.D2H,
            block_count=2,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.PRESERVE_COMPLETED,
            transfer=transfer(),
            direction=TransferDirection.D2H,
            bytes_moved=16,
            block_count=2,
            duration_ns=5,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.TRANSFER_STARTED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            bytes_moved=16,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.TRANSFER_COMPLETED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            bytes_moved=16,
            duration_ns=5,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.TRANSFER_FAILED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            terminal_reason=TransferTerminalReason.BACKEND_FAILURE,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.TRANSFER_CANCELLED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            terminal_reason=TransferTerminalReason.REQUEST_CANCELLED,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.RESTORE_STARTED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            block_count=2,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.RESTORE_COMPLETED,
            transfer=transfer(),
            direction=TransferDirection.H2D,
            receipt=receipt(),
            bytes_moved=16,
            block_count=2,
            duration_ns=5,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.RECOVERY_REQUEUED,
            requeue_reason=RecoveryRequeueReason.BLOCK_CAPACITY,
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.RECOVERY_ADMITTED,
            associated_transfers=(transfer(),),
            **common,
        ),
        KVTransferObservation(
            ObservationEvent.FIRST_COMPUTE,
            receipt=receipt(2),
            associated_transfers=(transfer(),),
            compute_kind=ComputeKind.PREFILL,
            **common,
        ),
    ]

    assert {record.event for record in records} == set(ObservationEvent)
    assert all(record.to_payload()["schema"].endswith(".v2") for record in records)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ObservationIdentity("", 0, 0), "request_id"),
        (lambda: ObservationIdentity("r" * 129, 0, 0), "128 bytes"),
        (lambda: ObservationIdentity("request", True, 0), "worker_generation"),
        (lambda: ObservationIdentity("request", 0, -1), "rank"),
        (lambda: ObservationIdentity("request", 0, 0, 0), "recovery_epoch"),
        (lambda: TransferIdentity("not-scoped"), "process_uuid:t"),
        (lambda: ReceiptIdentity(f"{PROCESS_UUID}:t:1"), "process_uuid:k"),
    ],
)
def test_identity_validation_is_bounded(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_event_rejects_unknown_or_mismatched_fields() -> None:
    with pytest.raises(ValueError, match="ObservationEvent"):
        KVTransferObservation(  # type: ignore[arg-type]
            "arbitrary_event",
            identity=identity(),
        )
    with pytest.raises(ValueError, match="closed schema"):
        KVTransferObservation(
            ObservationEvent.TRANSFER_STARTED,
            identity=identity(),
            transfer=transfer(),
            direction=TransferDirection.H2D,
            bytes_moved=16,
            block_count=1,
        )
    with pytest.raises(ValueError, match="d2h"):
        KVTransferObservation(
            ObservationEvent.PRESERVE_STARTED,
            identity=identity(),
            transfer=transfer(),
            direction=TransferDirection.H2D,
            block_count=1,
        )


def test_recovery_and_first_compute_require_exact_association() -> None:
    with pytest.raises(ValueError, match="recovery epoch"):
        KVTransferObservation(
            ObservationEvent.RECOVERY_REQUEUED,
            identity=identity(recovery=False),
            requeue_reason=RecoveryRequeueReason.UNCLASSIFIED,
        )
    with pytest.raises(ValueError, match="sorted and unique"):
        KVTransferObservation(
            ObservationEvent.FIRST_COMPUTE,
            identity=identity(),
            receipt=receipt(),
            associated_transfers=(transfer(2), transfer(1)),
            compute_kind=ComputeKind.DECODE,
        )
    with pytest.raises(ValueError, match="closed schema"):
        KVTransferObservation(
            ObservationEvent.FIRST_COMPUTE,
            identity=identity(),
            receipt=receipt(),
            compute_kind=ComputeKind.DECODE,
        )


def test_receipts_must_match_transfer_process_scope() -> None:
    foreign_process = "d" * 32
    with pytest.raises(ValueError, match="share process scope"):
        KVTransferObservation(
            ObservationEvent.RESTORE_COMPLETED,
            identity=identity(),
            transfer=transfer(),
            direction=TransferDirection.H2D,
            receipt=ReceiptIdentity(f"{foreign_process}:k:1"),
            bytes_moved=16,
            block_count=1,
            duration_ns=1,
        )
    with pytest.raises(ValueError, match="share process scope"):
        KVTransferObservation(
            ObservationEvent.FIRST_COMPUTE,
            identity=identity(),
            receipt=receipt(),
            associated_transfers=(TransferIdentity(f"{foreign_process}:t:1"),),
            compute_kind=ComputeKind.DECODE,
        )


def test_records_are_immutable() -> None:
    record = KVTransferObservation(
        ObservationEvent.TRANSFER_STARTED,
        identity=identity(),
        transfer=transfer(),
        direction=TransferDirection.D2H,
        bytes_moved=16,
    )
    with pytest.raises(FrozenInstanceError):
        record.bytes_moved = 32  # type: ignore[misc]
