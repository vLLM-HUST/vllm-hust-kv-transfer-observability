"""KV transfer observability primitives and an inert activation descriptor."""

from .descriptors import (
    ALLOWED_EVIDENCE_LABELS,
    DescriptorCaptureCounters,
    DescriptorInventory,
    DescriptorLayoutCapture,
    DescriptorRegion,
    EvidenceLabel,
)
from .events import EventSinkCounters, JsonlKVTransferEventSink
from .normalization import (
    CoreRecoveryAdmitted,
    CoreRecoveryRequeued,
    CoreTransferCancelled,
    CoreTransferCompleted,
    CoreTransferSubmitted,
    FirstComputeObserved,
    LifecycleNormalizer,
    NormalizationCounters,
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


class VllmHustKvTransferObservabilityContractProposal:
    """Metadata-only proposal; this class performs no runtime activation."""


__all__ = [
    "ALLOWED_EVIDENCE_LABELS",
    "ComputeKind",
    "CoreRecoveryAdmitted",
    "CoreRecoveryRequeued",
    "CoreTransferCancelled",
    "CoreTransferCompleted",
    "CoreTransferSubmitted",
    "DescriptorCaptureCounters",
    "DescriptorInventory",
    "DescriptorLayoutCapture",
    "DescriptorRegion",
    "EventSinkCounters",
    "EvidenceLabel",
    "FirstComputeObserved",
    "JsonlKVTransferEventSink",
    "KVTransferObservation",
    "LifecycleNormalizer",
    "NormalizationCounters",
    "ObservationEvent",
    "ObservationIdentity",
    "ReceiptIdentity",
    "RecoveryRequeueReason",
    "SourceHost",
    "TransferDirection",
    "TransferIdentity",
    "TransferOperation",
    "TransferTerminalReason",
    "VllmHustKvTransferObservabilityContractProposal",
]
