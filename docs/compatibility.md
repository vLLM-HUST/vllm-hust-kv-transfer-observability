# Compatibility and validation level

This document separates what CI actually executes from what the manifest merely
declares. Only the first table is evidence. Nothing in the second table may be
quoted as support, and no row may be added from a successful import, a passing
unit test, or a single smoke run.

Basis: `.github/workflows/ci.yml` at `53e6901` (`origin/main`), reviewed
2026-09-29.

## Verified by CI — every pull request and every push to `main`

| Dimension | Value | Evidence |
|---|---|---|
| Python | 3.10, 3.12, 3.14 | CI matrix; `requires-python = ">=3.10"` in `pyproject.toml` |
| Operating system | `ubuntu-latest` (Linux x86_64, GitHub-hosted) | CI runner |
| Extension Manager | commit `9fb467447e95d753f7002b28575d6802f4347181` | pinned in `EXTENSION_MANAGER_SPEC` |
| Install mode A | source checkout, editable | `pytest -q` in the checkout |
| Install mode B | built wheel in an isolated venv, suite run from outside the checkout | dedicated CI step |
| Packaging | sdist + wheel | `python -m build` |
| Lint | `ruff check`, `ruff format --check` | CI steps |
| Manager CLI | `extension inspect`, `extension check` | CI steps |
| Activation gate | `extension enable` exits 2 with `import_only` | asserted by CI |
| Runtime dependencies | none (`dependencies = []`, standard library only) | `pyproject.toml` |
| vLLM runtime | not imported, not started, not touched | nothing in CI imports `vllm` |
| Manager discovery | exactly one entry point, group `vllm_hust.extension_bundles` | `pyproject.toml` |

Filesystem semantics: the sink and capture paths require POSIX directory
descriptors, no-follow opens and permission bits, so the supported filesystem
targets are Linux/POSIX with the three Python versions above (see the README).
**Native Windows is not a supported filesystem test target**: those paths raise
`PermissionError [Errno 13]` at `events.py:85` / `descriptors.py:171` because
`os.open()` cannot open a directory there.

## Declared but NOT verified

| Dimension | Declared | Status |
|---|---|---|
| Host `vllm` version | `version_range: ">=0"` | placeholder; no vLLM revision has been exercised |
| Protocols | `vllm.kv-transfer.events.v2`, `.descriptors.v2`, `.identity.v1`, `.observer.v1` | proposed; no counterpart implementation verified |
| vLLM Ascend | — | untested; the Ascend call sites exist only on a parked branch |
| Hardware | — | no NPU/GPU validation of any kind |
| Models and features | — | none |
| macOS | — | untested |

## Why the host version range cannot be narrowed yet

`>=0` is a placeholder, not a claim. Replacing it with a tested range and
rejecting unsupported combinations (the wording in `source_inventory.md`)
requires at least one host revision to have been exercised end to end. None has:
the host-side outlet (`vLLM-HUST/vllm-hust#46`) is still open, and no available
environment serves vLLM with the paired revisions. Narrowing the range today
would be invention, not evidence.

## How to add a row

Only from a record produced under `tools/e2e_910b/` that carries the command, the
environment, the raw output and the commit (`result_log.md` template).
