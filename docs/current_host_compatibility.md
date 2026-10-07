# Current-host compatibility

Status: CPU host-contract acceptance on 2026-10-08. This is not NPU or
performance qualification.

## Accepted pairing

| Component | Revision | Evidence |
|---|---|---|
| `vLLM-HUST/vllm-hust` observer branch | `c2e9e7d53` on current main `ebfcfba65` | public `vllm.kv-transfer.observer.v1`, capability registry version `1.0`, host-owned runtime evidence |
| this extension | Manifest 0.3 work based on `c206195` | active general-plugin carrier and clean-wheel host binding |
| Extension Manager | `5aa797cdc` | discover/check/enable/plan/render and live-process evidence validation |

The host change is tracked by
[vllm-hust#46](https://github.com/vLLM-HUST/vllm-hust/pull/46). The extension
must not be merged or advertised as compatible before that host contract lands
on `main` at the tested version.

## Results

- Host focused capability/observer/recovery/descriptor tests: 46 passed.
- Full host offloading-connector CPU regression: 603 passed, 2 skipped.
- Host observer/evidence follow-up: 28 focused tests passed; changed-file Ruff,
  mypy, SPDX, forbidden-import, and repository hooks passed.
- Extension tests: 146 passed; Ruff check and format passed.
- Clean wheel: ECPA inspect/check/enable/plan/render completed from an isolated
  Python 3.12 environment.
- Installed-wheel host seam: one H2D submission and completion produced
  `restore_started` and `restore_completed`; two bound runtime-effective host
  receipts were delivered; unregister left no observer.
- Live-process rule: ECPA reported `runtime_effective` while the owning process
  was alive and removed that state after the same process stopped.

No model server, port, child worker, or accelerator was started for these
checks.

## Fail-closed boundary

The active binding accepts worker-local `transfer_submitted`,
`transfer_completed`, and `transfer_cancelled` records. It rejects unsupported
API versions and any record without a valid request, rank, job, operation, or
prior submission.

The following are not qualified:

- cross-process recovery admission and first-compute correlation;
- descriptor capture from core or Ascend copy sites;
- the Ascend V1 runner and `cpu_npu.py` follow-up call sites;
- device transfer correctness, resource release, overhead, or performance.

These omissions are intentional. A scheduler job id is not treated as a
process-scoped transfer identity, and historical B134 compatibility is not
described as current native NPU validation.
