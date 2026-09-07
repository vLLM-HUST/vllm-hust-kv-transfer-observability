"""KV transfer observability primitives and an inert activation descriptor."""

from .adapter import (
    HOST_OBSERVER_CONTRACT,
    AdapterActivationError,
    AdapterCounters,
    AdapterState,
    HostObserverBinding,
    HostObserverCallbacks,
    KVTransferHostAdapter,
)
from .b134 import (
    B134_EVENT_CONTRACTS,
    B134_EVENT_COUNT,
    B134_RECOVERY_CHAIN,
    UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE,
    B134Event,
    B134EventContract,
    B134IdentityKind,
    B134Owner,
    b134_event_contract,
)
from .config import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    MAX_SHUTDOWN_TIMEOUT_SECONDS,
    ConfigurationError,
    ObserverConfig,
)
from .descriptors import (
    ALLOWED_EVIDENCE_LABELS,
    DescriptorCaptureCounters,
    DescriptorInventory,
    DescriptorLayoutCapture,
    DescriptorRegion,
    EvidenceLabel,
)
from .events import MIN_RECORD_BYTES, EventSinkCounters, JsonlKVTransferEventSink
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
    "HOST_OBSERVER_CONTRACT",
    "AdapterActivationError",
    "AdapterCounters",
    "AdapterState",
    "B134_EVENT_CONTRACTS",
    "B134_EVENT_COUNT",
    "B134_RECOVERY_CHAIN",
    "UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE",
    "B134Event",
    "B134EventContract",
    "B134IdentityKind",
    "B134Owner",
    "ComputeKind",
    "ConfigurationError",
    "CoreRecoveryAdmitted",
    "CoreRecoveryRequeued",
    "CoreTransferCancelled",
    "CoreTransferCompleted",
    "CoreTransferSubmitted",
    "DescriptorCaptureCounters",
    "DescriptorInventory",
    "DescriptorLayoutCapture",
    "DescriptorRegion",
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "EventSinkCounters",
    "EvidenceLabel",
    "FirstComputeObserved",
    "HostObserverBinding",
    "HostObserverCallbacks",
    "JsonlKVTransferEventSink",
    "KVTransferObservation",
    "LifecycleNormalizer",
    "MAX_SHUTDOWN_TIMEOUT_SECONDS",
    "MIN_RECORD_BYTES",
    "NormalizationCounters",
    "ObservationEvent",
    "ObservationIdentity",
    "ObserverConfig",
    "ReceiptIdentity",
    "RecoveryRequeueReason",
    "SourceHost",
    "TransferDirection",
    "TransferIdentity",
    "TransferOperation",
    "TransferTerminalReason",
    "KVTransferHostAdapter",
    "VllmHustKvTransferObservabilityContractProposal",
    "b134_event_contract",
]
