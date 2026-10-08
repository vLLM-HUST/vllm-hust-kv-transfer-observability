# Current-host compatibility

Status: CPU host-contract acceptance on 2026-10-08. This is not NPU or
performance qualification. The merged host contract is limited by the
cross-process identity described below.

## Accepted pairing

| Component | Revision | Evidence |
|---|---|---|
| `vLLM-HUST/vllm-hust` | observer head `c2e9e7d53`, merged as `115e5f6b0d` | public `vllm.kv-transfer.observer.v1`, capability registry version `1.0`, host-owned runtime evidence |
| this extension | Manifest 0.3 on main `529ab28` | active general-plugin carrier and clean-wheel host binding |
| Extension Manager | `5aa797cdc` | discover/check/enable/plan/render and live-process evidence validation |

The host change in [vllm-hust#46](https://github.com/vLLM-HUST/vllm-hust/pull/46)
and the narrow carrier in [extension#10](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/pull/10)
are merged. Their CPU evidence does not establish serving or NPU compatibility.

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

The follow-up binding generates a nonzero, per-process incarnation and keeps
only bounded in-flight job associations. A rejected submission creates no
association; a mismatched terminal record cannot close another request's job.
The worker-only entry point releases completed H2D receipt state because it
cannot consume scheduler admissions. This allows long-running transfer-only
observation without claiming a complete recovery chain. A process-local
incarnation is not a host-supplied cross-process generation.

At the 2026-10-08 read-only host main `c3e05329b7`, scheduler requeue and
admission records carry request ID, epoch, and job roster, but no worker rank
or generation. Worker transfer records carry job ID, rank, and request ID but
no recovery epoch. Worker first-compute carries epoch, job roster and rank,
but not the corresponding accepted per-worker transfer receipts. The copy
site emits region-relative descriptors before the worker's submission event
with `rank=None` and no request ID. Job IDs alone do not prove that these
observations came from the same worker incarnation or recovery episode.
The plugin therefore keeps those paths detached. Completing the chain needs
the host to propagate one bounded process/generation and episode association
across scheduler, copy, and worker callbacks; no timestamp or filename join
is substituted for that field.

The following are not qualified:

- cross-process recovery admission and first-compute correlation;
- descriptor capture from core or Ascend copy sites;
- the Ascend V1 runner and `cpu_npu.py` follow-up call sites;
- device transfer correctness, resource release, overhead, or performance.

The target machine exposes an idle, healthy 910B2 and CANN 9.1.0. This is
hardware availability, not a validated runtime: the isolated Ascend CPU
preflight still lacks pinned `triton-ascend==3.6.0` and both runner imports
fail with a `DeviceOperator` circular import. Ascend main was refreshed to
`7c8ec865a1` for source review only. No matched, runnable core/Ascend pair,
model, KV-transfer service, or real NPU observation has been demonstrated.
Correctness and matched overhead measurements remain pending that runtime
selection and the identity-bearing host seam.

These omissions are intentional. A scheduler job id is not treated as a
process-scoped transfer identity, and historical B134 compatibility is not
described as current native NPU validation.
