# 910B acceptance tools for Issue #2

Read [PREFLIGHT.md](PREFLIGHT.md) for the current host, Ascend, and hardware
status. This directory contains environment and package checks plus the Tier 2
driver; the driver itself has not yet been executed on a qualified pair.

| Tier | Command | What it checks |
| --- | --- | --- |
| 0 | `bash 01_env_probe.sh` | Hardware and installed software inventory. |
| 1 | `bash 02_package_check.sh /path/to/plugin.whl` | Exact wheel installation, dependency check, default-off Manifest, real host capability check, and isolated Extension Manager enable/plan/render/disable. |
| 2 | `python 03_tier2_driver.py --python ... --model ... --out ...` | Real transfer, recovery, descriptor, fault, and teardown behavior on a qualified core/Ascend pair. |
| 3 | Not yet provided | Repeated matched enabled/disabled overhead measurements after Tier 2 passes. |

Tier 1 uses `KV_OBSERVER_PYTHON` for the target virtual environment's Python,
`KV_OBSERVER_UV` for `uv`, and an optional `KV_OBSERVER_MANAGER_REF` for an
already reviewed local Extension Manager checkout. Its default Manager ref is
the pinned commit `5aa797cdc`. Capture full output and exact revisions with
[result_log.md](result_log.md). A degraded compatibility result or dependency
conflict is a failed check even when the Manager CLI itself exits zero.

## Tier 2 driver

`03_tier2_driver.py` starts a real OpenAI server with the CPU offloading
connector and the plugin bundle enabled explicitly, drives a store phase, a
GPU-prefix-cache reset, a reload phase, and a concurrent pressure phase,
records the graceful teardown, and then validates the raw artifacts with the
installed package's own validator.

```bash
python 03_tier2_driver.py \
  --python /venv/bin/python \
  --model /models/target \
  --out /evidence/tier2-<date> \
  --tensor-parallel-size 1 \
  --num-gpu-blocks-override 64
```

It exits 0 only when every check passes, and it never reports a pass for
evidence it did not collect:

- the target interpreter must expose the plugin's required host contract
  (`REQUIRED_HOST_API_VERSION`, `CORRELATED_HOST_API_VERSION`) exactly, so a
  1.0 host or an unknown version fails closed before the server starts;
- the descriptor capture path is enabled by default
  (`canonical_layout: true`), and a run without descriptor inventories fails;
- at least one request the driver sent must complete a full restore chain
  under `python -m vllm_hust_kv_transfer_observability.validation`
  `--expect-restore-chain`;
- `--require-clean-teardown` turns the `force killing`/`resource_tracker`
  observations into a failing check (see host issue #54).

`--dry-run` prints the exact server argv, environment, and workload plan
without starting anything. `manifest.json` in the output directory records the
argv, environment, probe, per-check status, event summary, validation output,
teardown observations, and the SHA256 of every artifact.

The legacy `verify_events.py` is not a v2 qualification validator. Retain raw
event files and use the installed package's schema/lifecycle validator:
`python -m vllm_hust_kv_transfer_observability.validation --events
/path/to/events.jsonl`. A complete-chain check also needs
`--expect-restore-chain REQUEST_ID`; schema validation alone proves no
recovery episode.
