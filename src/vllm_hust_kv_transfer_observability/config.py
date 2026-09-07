# SPDX-License-Identifier: Apache-2.0
"""Closed, fail-closed configuration for explicit observer attachment."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from .descriptors import (
    DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES,
    MAX_DESCRIPTOR_REGIONS,
    EvidenceLabel,
)
from .events import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_PENDING_RECORDS,
    DEFAULT_MAX_RECORD_BYTES,
    MIN_RECORD_BYTES,
)
from .normalization import (
    DEFAULT_MAX_CORRELATED_TRANSFERS,
    DEFAULT_MAX_RECOVERY_ADMISSIONS,
)
from .schema import MAX_TRANSFER_ASSOCIATIONS

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
MAX_SHUTDOWN_TIMEOUT_SECONDS = 30.0


class ConfigurationError(ValueError):
    """Configuration is unknown, malformed, or unsafe for activation."""


def _bounded_int(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be within {minimum}..{maximum}")


def _path(value: object, name: str) -> Path | None:
    if value is None:
        return None
    if type(value) is not str or not value or "\x00" in value:
        raise ConfigurationError(f"{name} must be a nonempty path string")
    return Path(value)


@dataclass(frozen=True, slots=True)
class ObserverConfig:
    """Validated configuration whose accepted keys and values are closed."""

    enabled: bool = False
    event_path: Path | None = None
    descriptor_dir: Path | None = None
    evidence_label: EvidenceLabel | None = None
    max_pending_records: int = DEFAULT_MAX_PENDING_RECORDS
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_correlated_transfers: int = DEFAULT_MAX_CORRELATED_TRANSFERS
    max_recovery_admissions: int = DEFAULT_MAX_RECOVERY_ADMISSIONS
    max_descriptor_regions: int = MAX_DESCRIPTOR_REGIONS
    max_descriptor_record_bytes: int = DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES
    shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ConfigurationError("enabled must be a bool")
        for value, name in (
            (self.event_path, "event_path"),
            (self.descriptor_dir, "descriptor_dir"),
        ):
            if value is not None and not isinstance(value, Path):
                raise ConfigurationError(f"{name} must be a Path or None")
        if (
            self.evidence_label is not None
            and type(self.evidence_label) is not EvidenceLabel
        ):
            raise ConfigurationError("evidence_label must be an EvidenceLabel or None")
        if self.enabled and self.event_path is None:
            raise ConfigurationError("enabled configuration requires event_path")
        if (self.descriptor_dir is None) != (self.evidence_label is None):
            raise ConfigurationError(
                "descriptor_dir and evidence_label must be configured together"
            )
        _bounded_int(
            self.max_pending_records,
            "max_pending_records",
            1,
            DEFAULT_MAX_PENDING_RECORDS,
        )
        _bounded_int(
            self.max_record_bytes,
            "max_record_bytes",
            MIN_RECORD_BYTES,
            DEFAULT_MAX_RECORD_BYTES,
        )
        _bounded_int(
            self.max_file_bytes,
            "max_file_bytes",
            self.max_record_bytes,
            DEFAULT_MAX_FILE_BYTES,
        )
        _bounded_int(
            self.max_correlated_transfers,
            "max_correlated_transfers",
            1,
            MAX_TRANSFER_ASSOCIATIONS,
        )
        _bounded_int(
            self.max_recovery_admissions,
            "max_recovery_admissions",
            1,
            MAX_TRANSFER_ASSOCIATIONS,
        )
        _bounded_int(
            self.max_descriptor_regions,
            "max_descriptor_regions",
            1,
            MAX_DESCRIPTOR_REGIONS,
        )
        _bounded_int(
            self.max_descriptor_record_bytes,
            "max_descriptor_record_bytes",
            1,
            DEFAULT_MAX_DESCRIPTOR_RECORD_BYTES,
        )
        timeout = self.shutdown_timeout_seconds
        if (
            type(timeout) not in {int, float}
            or not math.isfinite(timeout)
            or not 0 <= timeout <= MAX_SHUTDOWN_TIMEOUT_SECONDS
        ):
            raise ConfigurationError(
                "shutdown_timeout_seconds must be within "
                f"0..{MAX_SHUTDOWN_TIMEOUT_SECONDS}"
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ObserverConfig:
        """Parse a JSON-like mapping without accepting aliases or unknown keys."""
        if not isinstance(raw, Mapping):
            raise ConfigurationError("observer configuration must be a mapping")
        allowed = frozenset(field.name for field in fields(cls))
        if any(type(key) is not str for key in raw):
            raise ConfigurationError("configuration keys must be strings")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ConfigurationError(
                "unknown configuration keys: " + ", ".join(unknown)
            )

        values = dict(raw)
        for name in ("event_path", "descriptor_dir"):
            if name in values:
                values[name] = _path(values[name], name)
        if "evidence_label" in values:
            value = values["evidence_label"]
            try:
                values["evidence_label"] = (
                    None if value is None else EvidenceLabel(value)
                )
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    f"unsupported evidence_label: {value}"
                ) from exc
        return cls(**values)


__all__ = [
    "ConfigurationError",
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "MAX_SHUTDOWN_TIMEOUT_SECONDS",
    "ObserverConfig",
]
