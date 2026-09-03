"""KV transfer observability primitives and an inert activation descriptor."""

from .descriptors import (
    ALLOWED_EVIDENCE_LABELS,
    DESCRIPTOR_SCHEMA,
    DescriptorLayoutCapture,
)
from .events import EVENT_VOCABULARY_V1, JsonlKVTransferEventSink


class VllmHustKvTransferObservabilityContractProposal:
    """Metadata-only proposal; this class performs no runtime activation."""


__all__ = [
    "ALLOWED_EVIDENCE_LABELS",
    "DESCRIPTOR_SCHEMA",
    "EVENT_VOCABULARY_V1",
    "DescriptorLayoutCapture",
    "JsonlKVTransferEventSink",
    "VllmHustKvTransferObservabilityContractProposal",
]
