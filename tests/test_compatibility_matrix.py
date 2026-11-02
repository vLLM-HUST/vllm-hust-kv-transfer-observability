"""Pin the compatibility matrix to the manifest, packaging metadata, and CI.

The matrix is documentation, but its host ranges, Python matrix, and protocol
versions must not drift from the files that actually enforce them. These tests
fail on drift, and they require every row to carry evidence or an explicit
unqualified reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = REPOSITORY_ROOT / "docs" / "compatibility_matrix.json"
MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "src"
    / "vllm_hust_kv_transfer_observability"
    / "vllm-hust-extension-v0.3.json"
)
CI_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = REPOSITORY_ROOT / "pyproject.toml"

REQUIRED_DIMENSIONS = {
    "host",
    "host-api",
    "python",
    "ascend",
    "hardware",
    "model",
    "feature",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows() -> list[dict]:
    return _load(MATRIX_PATH)["rows"]


def _row(dimension: str, value: str) -> dict:
    matches = [
        row
        for row in _rows()
        if row["dimension"] == dimension and row["value"] == value
    ]
    assert len(matches) == 1, f"{dimension}/{value} must appear exactly once"
    return matches[0]


def test_matrix_covers_every_required_dimension_once_per_cell() -> None:
    matrix = _load(MATRIX_PATH)

    assert matrix["schema_version"] == "vllm-hust-compatibility-matrix-v1"
    assert matrix["matrix_id"] == (
        "org.vllm-hust.kv-transfer-observability/compatibility"
    )
    assert set(matrix["dimensions"]) == REQUIRED_DIMENSIONS
    assert {row["dimension"] for row in matrix["rows"]} == REQUIRED_DIMENSIONS

    cells = [(row["dimension"], row["value"]) for row in matrix["rows"]]
    assert len(cells) == len(set(cells))


def test_every_row_is_evidence_labelled() -> None:
    states = _load(MATRIX_PATH)["states"]

    for row in _rows():
        assert row["state"] in states, row
        assert not Path(row["reference"]).is_absolute(), row
        assert (REPOSITORY_ROOT / row["reference"]).exists(), row
        if row["state"] == "unqualified":
            assert row["reason"].strip(), row
        else:
            assert row["evidence"].strip(), row


def test_host_rows_match_the_manifest() -> None:
    manifest = _load(MANIFEST_PATH)
    host = manifest["host"]

    host_row = _row("host", "vllm >=0.29.1,<0.30")
    assert host["version_range"] in host_row["value"]
    assert host["api_range"] == ">=1,<2"

    protocols = {item["name"]: item["version_range"] for item in manifest["protocols"]}
    observer = next(
        row
        for row in _rows()
        if row["dimension"] == "host-api"
        and row["value"].startswith("vllm.kv-transfer.observer ")
    )
    assert observer["value"].split(" ", 1)[1] == protocols["vllm.kv-transfer.observer"]


def test_python_rows_match_packaging_and_ci() -> None:
    ci_matrix = re.search(
        r"python:\s*\[([^\]]+)\]", CI_PATH.read_text(encoding="utf-8")
    )
    assert ci_matrix is not None, "CI python matrix not found"
    ci_versions = {
        version.strip().strip('"') for version in ci_matrix.group(1).split(",")
    }

    verified = {
        row["value"]
        for row in _rows()
        if row["dimension"] == "python" and row["state"] == "verified"
    }
    assert verified == ci_versions

    floor = re.search(
        r'requires-python\s*=\s*">=([0-9.]+)"',
        PYPROJECT_PATH.read_text(encoding="utf-8"),
    )
    assert floor is not None, "requires-python not found"
    assert floor.group(1) in verified
