import pytest

from vllm_hust_kv_transfer_observability import (
    B134_EVENT_CONTRACTS,
    B134_EVENT_COUNT,
    B134_RECOVERY_CHAIN,
    UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE,
    B134Event,
    B134IdentityKind,
    B134Owner,
    ObservationEvent,
    b134_event_contract,
)


def test_b134_vocabulary_is_the_exact_legacy_14_event_set() -> None:
    assert {event.value for event in B134Event} == {
        "preempt",
        "wakeup",
        "admission",
        "scheduled",
        "restore_start",
        "restore_done",
        "cpu_store",
        "cpu_evict",
        "evict",
        "swap_d2h_submit",
        "gather_h2d",
        "transfer_submit",
        "copy_observed_complete",
        "sched_step",
    }
    assert len(B134Event) == B134_EVENT_COUNT == 14
    assert set(B134_EVENT_CONTRACTS) == set(B134Event)
    assert UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE == 15


def test_b134_event_ownership_matches_legacy_call_sites() -> None:
    scheduler_events = {
        event
        for event, contract in B134_EVENT_CONTRACTS.items()
        if contract.owner is B134Owner.SCHEDULER
    }
    assert scheduler_events == {
        B134Event.PREEMPT,
        B134Event.WAKEUP,
        B134Event.ADMISSION,
        B134Event.SCHEDULED,
    }
    assert all(
        B134_EVENT_CONTRACTS[event].owner is B134Owner.KV_OFFLOAD
        for event in set(B134Event) - scheduler_events
    )


def test_b134_recovery_chain_preserves_legacy_order() -> None:
    assert B134_RECOVERY_CHAIN == (
        B134Event.PREEMPT,
        B134Event.RESTORE_START,
        B134Event.RESTORE_DONE,
        B134Event.WAKEUP,
        B134Event.ADMISSION,
        B134Event.SCHEDULED,
    )


def test_b134_fields_and_identity_kinds_match_legacy_payloads() -> None:
    assert b134_event_contract(B134Event.TRANSFER_SUBMIT).fields == (
        "bytes",
        "dependency_us",
        "descriptor_us",
        "descriptors",
        "direction",
        "submit_us",
    )
    assert b134_event_contract(B134Event.COPY_OBSERVED_COMPLETE).fields == (
        "bytes",
        "completion_observed_ms",
        "device_event_ms",
        "direction",
    )
    assert b134_event_contract(B134Event.CPU_EVICT).identity_kind is (
        B134IdentityKind.SYSTEM
    )
    assert b134_event_contract(B134Event.SCHED_STEP).identity_kind is (
        B134IdentityKind.SYSTEM
    )
    assert b134_event_contract(B134Event.RESTORE_START).identity_kind is (
        B134IdentityKind.REQUEST
    )


def test_only_direct_lifecycle_correspondences_are_declared() -> None:
    mapped = {
        event: contract.canonical_event
        for event, contract in B134_EVENT_CONTRACTS.items()
        if contract.canonical_event is not None
    }
    assert mapped == {
        B134Event.RESTORE_START: ObservationEvent.RESTORE_STARTED,
        B134Event.RESTORE_DONE: ObservationEvent.RESTORE_COMPLETED,
        B134Event.TRANSFER_SUBMIT: ObservationEvent.TRANSFER_STARTED,
        B134Event.COPY_OBSERVED_COMPLETE: ObservationEvent.TRANSFER_COMPLETED,
    }


def test_unknown_event_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        B134Event("unknown")
    with pytest.raises(ValueError, match="B134Event"):
        b134_event_contract("restore_start")  # type: ignore[arg-type]
