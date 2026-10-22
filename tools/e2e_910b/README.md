# 910B acceptance tools for Issue #2

Read [PREFLIGHT.md](PREFLIGHT.md) for the current host, Ascend, and hardware
status. This directory currently contains environment and package checks;
it does not contain a reproducible Tier 2/3 serving or performance driver.

| Tier | Command | What it checks |
| --- | --- | --- |
| 0 | `bash 01_env_probe.sh` | Hardware and installed software inventory. |
| 1 | `bash 02_package_check.sh /path/to/plugin.whl` | Exact wheel installation, dependency check, default-off Manifest, real host capability check, and isolated Extension Manager enable/plan/render/disable. |
| 2 | Not yet provided | Real transfer, recovery, descriptor, fault, and teardown behavior on a qualified core/Ascend pair. |
| 3 | Not yet provided | Repeated matched enabled/disabled overhead measurements after Tier 2 passes. |

Tier 1 uses `KV_OBSERVER_PYTHON` for the target virtual environment's Python,
`KV_OBSERVER_UV` for `uv`, and an optional `KV_OBSERVER_MANAGER_REF` for an
already reviewed local Extension Manager checkout. Its default Manager ref is
the pinned commit `5aa797cdc`. Capture full output and exact revisions with
[result_log.md](result_log.md). A degraded compatibility result or dependency
conflict is a failed check even when the Manager CLI itself exits zero.

The historical `verify_events.py` is not a v2 qualification validator.
Retain raw event files and use the installed package's schema/lifecycle
validator: `python -m vllm_hust_kv_transfer_observability.validation
--events /path/to/events.jsonl`. A complete-chain check also needs
`--expect-restore-chain REQUEST_ID`; schema validation alone proves no
recovery episode.
