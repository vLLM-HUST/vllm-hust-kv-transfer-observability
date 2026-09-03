# SPDX-License-Identifier: Apache-2.0
"""Address-free descriptor inventory capture extracted from legacy PR #220."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ALLOWED_EVIDENCE_LABELS = {
    "existing-server-probe",
    "real-online",
    "replay",
    "simulation/model",
}

DESCRIPTOR_SCHEMA = "vllm-hust.kv-transfer-descriptor-layout.v1"


class DescriptorLayoutCapture:
    def __init__(self, capture_dir: str | Path, evidence_label: str) -> None:
        self.capture_dir = Path(capture_dir)
        if not self.capture_dir.is_dir():
            raise ValueError(f"capture directory does not exist: {self.capture_dir}")
        if evidence_label not in ALLOWED_EVIDENCE_LABELS:
            raise ValueError(f"unsupported evidence label: {evidence_label}")
        self.evidence_label = evidence_label

    def capture(
        self,
        *,
        job_id: int,
        direction: str,
        descriptors: list[dict[str, Any]],
    ) -> Path:
        if direction not in {"d2h", "h2d"}:
            raise ValueError(f"unsupported direction: {direction}")
        if not descriptors:
            raise ValueError("descriptor inventory is empty")
        normalized: list[dict[str, int | str]] = []
        for descriptor in descriptors:
            required = {"src_offset", "dst_offset", "size", "direction"}
            if set(descriptor) != required:
                raise ValueError("descriptor fields do not match the v1 schema")
            src_offset = descriptor["src_offset"]
            dst_offset = descriptor["dst_offset"]
            size = descriptor["size"]
            if not all(type(value) is int for value in (src_offset, dst_offset, size)):
                raise ValueError("descriptor offsets and size must be integers")
            if src_offset < 0 or dst_offset < 0 or size <= 0:
                raise ValueError("descriptor offsets/size are invalid")
            if descriptor["direction"] != direction:
                raise ValueError("descriptor direction changed within one inventory")
            normalized.append(
                {
                    "src_offset": src_offset,
                    "dst_offset": dst_offset,
                    "size": size,
                    "direction": direction,
                }
            )
        output = self.capture_dir / f"{os.getpid()}-{direction}-job{job_id}.json"
        payload = {
            "descriptors": normalized,
            "direction": direction,
            "evidence_label": self.evidence_label,
            "job_id": job_id,
            "schema": DESCRIPTOR_SCHEMA,
        }
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            if os.write(fd, data) != len(data):
                raise OSError("short descriptor inventory write")
        finally:
            os.close(fd)
        return output


__all__ = ["ALLOWED_EVIDENCE_LABELS", "DESCRIPTOR_SCHEMA", "DescriptorLayoutCapture"]
