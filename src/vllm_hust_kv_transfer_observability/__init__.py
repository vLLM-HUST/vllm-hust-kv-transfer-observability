"""KV transfer observability primitives and an inert activation descriptor."""

from .descriptors import ALLOWED_EVIDENCE_LABELS, DescriptorLayoutCapture
from .events import JsonlKVTransferEventSink


class VllmHustKvTransferObservabilityContractProposal:
    """Metadata-only proposal; this class performs no runtime activation."""


__all__ = [
    "ALLOWED_EVIDENCE_LABELS",
    "DescriptorLayoutCapture",
    "JsonlKVTransferEventSink",
    "VllmHustKvTransferObservabilityContractProposal",
]
