# Current-host compatibility

## Local 910B2 NPU diagnostic (2026-10-08, uncommitted)

The selected baseline remains organization core main `c3e05329b7` with the
local v2 observer seam. An isolated Ascend worktree at `runtime-npu/ascend`
starts from HUST main `575bd1c265`. Its three local source changes skip
unavailable `fla_npu` imports for models not used here, skip the V2-only import
of core's removed `gpu.spec_decode.autoregressive` module, and call
`observe_forward_batch` at the real Ascend V1 forward boundary. The last
change matches the intent of collaborator branch
`feat/kv-transfer-observability-call-sites`; it is not yet integrated into
Ascend main. The plugin was installed from this worktree's locally built wheel.
All of this remains local; the original Ascend worktree is unchanged.

This is an **experimental single-model pairing**, not a resolved package
compatibility matrix. The candidate uses Python 3.12, Torch 2.13.0+cpu,
TorchNPU 2.13.0rc1, Transformers 5.19.0, CANN 9.1.0, and the available
Triton-Ascend 3.2.2 on a 910B2. Ascend still declares Transformers 5.14.1
and Triton-Ascend 3.6.0. The latter was not found in the checked package
indexes. `uv pip check` reports ten incompatibilities, including those two,
Triton-Ascend 3.2.2's older dependency pins, and missing Triton 3.5.0.
A direct CMake build supplied the `vllm_ascend_C` binding and
`swap_blocks_batch` for this diagnostic. The full Ascend custom-operator
package build was stopped after about 28 minutes while compiling unrelated
operator variants; it did not qualify an installable Ascend release.

Real-device evidence in `runtime-npu/`:

| Probe | Observed result |
|---|---|
| Model baseline | Qwen3-0.6B loaded on NPU and generated two tokens; clean exit. |
| CPU KV offload/restore | A cold prompt, prefix-cache reset, and CPU restore produced identical token IDs `[4236, 11]`. With plugin activation in a spawned EngineCore, JSONL contains D2H and H2D start/completion plus a host transfer receipt; each copy moved 14,680,064 bytes. |
| Recovery pressure | With 80 MiB NPU KV cache, three 129-token prompts, 145 generated tokens, and GPU prefix caching disabled, one request was preempted twice. The plugin wrote 30 records; the validator accepted **two complete v2 restore episodes** for the same request, epochs 1 and 2, jobs 2 and 4, exact rank-0 Worker-generation rosters, admissions, and first compute. All three requests finished with the same output digests as the corresponding prefix-cached run. |
| Failure | A temporary, explicit H2D-copy exception produced `EngineDeadError`. Only the prior successful D2H preserve records were written; no restore completion was invented. The injection was removed. This reveals a serving-failure path, not graceful recovery from a backend fault. |
| Disabled/unregistered plugin | Starting with the bundle disabled completed the same real KV restore with identical token IDs and no event file. In a separate live process, one restore wrote five records; `stop_plugin()` on the Worker returned success, a second restore completed, and the record count remained five. |
| Timing probe | Six restores per condition, first sample excluded: disabled median 171.341 ms, enabled median 183.982 ms (12.641 ms / 7.4% difference). This is one sequential, small-sample run and is **not** a qualified steady-state overhead estimate. |

The subsequent local fork-lifecycle fix reinitializes only general plugins
that opt in after a fork. Re-running all inherited general plugins made Ascend
register `MooncakeConnectorV1` twice and prevented EngineCore startup. The
host's v1/v2 observer registries now discard inherited callbacks and locks;
this plugin closes copied writer descriptors without joining vanished parent
threads before the child attaches its own adapter. With the local core fix and
rebuilt plugin wheel, the default `fork` run restored identical token IDs and
wrote five real D2H/H2D/receipt records. In another forked NPU process,
`stop_plugin()` on the Worker returned true after the first restore wrote five
records; the second restore succeeded and the count remained five. The live
unregister test invoked a local Worker callable through vLLM RPC, requiring
`VLLM_ALLOW_INSECURE_SERIALIZATION=1` only for that isolated test.
The same three-request/80 MiB/no-prefix pressure workload under default fork
wrote 30 records; offline validation accepted two complete v2 recovery
episodes for one request. Its three output digests matched the earlier spawn
run exactly.
With the bundle disabled under default fork, the same simple restore
completed with matching token IDs and created no event file.

The earlier five-second force-kill in `spawn` also reproduced with the plugin
disabled. With an explicit 30-second process-manager grace, disabled and
enabled spawn runs both finished cleanly after roughly five to seven seconds
of EngineCore teardown; the enabled run wrote five records. This points to the
host's default five-second NPU cleanup window rather than plugin ownership,
but it does not establish clean teardown for every model or device. The
30-second grace is a diagnostic call to `engine_core.shutdown(timeout=30)`,
not a plugin configuration or a claimed production default.

The complete-chain result covers one 910B2, one dense model, the Ascend V1
runner, CPU offload, and this local v2 observer. It does not cover multi-rank
receipt aggregation, descriptor layout capture, V2 runner, broad model or
kernel compatibility, graceful backend-failure recovery, all process exits,
package dependency resolution, or release-grade performance. The previously
stated omissions below describe the published v1 baseline and pre-diagnostic
checks.

## Local v2 follow-up (uncommitted, 2026-10-08)

The current-core worktree at `runtime-current/core` now has a local
`feature/kv-observer-v2-correlation` branch. It keeps #46's v1.0 observer
unchanged and adds an optional `vllm.kv-transfer.observer.v2` protocol. The
scheduler issues an incarnation token and carries the recovery epoch on load
jobs. Workers issue their own incarnation tokens, attach them to completed
load metadata, and report first compute with the exact accepted roster sent
back by the scheduler. The plugin binds both interfaces and writes v2 facts
as `vllm-hust.kv-transfer-correlated-host.v2`; v1 facts stay separate.

The focused core tests and plugin source tests pass. At this earlier
checkpoint, a synthetic public-API smoke wrote six v2 records and validated
one complete chain without executing a connector, model forward, or NPU copy;
the real-device evidence is reported above. The v2 seam has not been merged
or published. The Manifest still
requires only the published v1 protocol, so an older host can produce
Worker-local observations but cannot prove a v2 chain. Copy-site descriptor
and Ascend V1 first-forward gaps remain open. Current core is the priority;
Ascend remains a separate compatibility candidate.

The rest of this document records the published v1 baseline and earlier
compatibility investigation. Statements that the stronger identity seam is
absent refer to that baseline.

Status: current-main package and CPU observer checks refreshed on 2026-10-08.
Current `vllm-hust` main is the fixed baseline. The isolated CPU environment
now has compatible installed dependencies with Ascend removed; Ascend is a
separate, unqualified candidate. Serving, NPU, and performance remain
unqualified. The merged contract lacks the shared cross-process identity
needed for strict per-Worker attribution.

## Published host-contract baseline

| Component | Revision | Evidence |
|---|---|---|
| `vLLM-HUST/vllm-hust` | current main `c3e05329b7`; #46 head `c2e9e7d53` merged as `115e5f6b0d` | public `vllm.kv-transfer.observer.v1`, capability registry version `1.0`, host-owned runtime evidence |
| this extension | Manifest 0.3 on main `529ab28` | active general-plugin carrier and clean-wheel host binding |
| Extension Manager | `5aa797cdc` | discover/check/enable/plan/render and live-process evidence validation |

The host change in [vllm-hust#46](https://github.com/vLLM-HUST/vllm-hust/pull/46)
and the narrow carrier in [extension#10](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/pull/10)
are merged. #46 completed the published v1.0 host outlet: real transfer,
receipt, recovery requeue/admission, descriptor, and first-compute callbacks.
#10 deliberately consumes only Worker-local submit/complete/cancel callbacks.
This plugin limitation must not be described as an unfinished #46 host seam.
Their CPU evidence does not establish serving or NPU compatibility. The
Manifest's host range is `>=0.29.1,<0.30`. Earlier raw `setuptools_scm`
checks incorrectly suggested that #46 and current main were outside this
range: the fork's `setup.py` uses a custom version scheme. Editable non-device
builds of the exact sources produce
`0.29.1.post1.dev463+gc2e9e7d53.empty` for #46 head and
`0.29.1.post1.dev522+gc3e05329b.empty` for current main; both satisfy the
range. Current-main version acceptance therefore needs no Manifest change.
This is package metadata acceptance, not a complete core/Ascend runtime matrix.

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
- Before separation, current-main editable non-device builds in the isolated
  CPU preflight environment passed core `c3e05329b7` observer/recovery tests
  42/42 and Ascend `575bd1c265` native CPU-offload tests 13/13. The Ascend
  package was subsequently removed to make the core-first environment's
  dependency check pass; the Ascend source worktree remains available.
- Plugin source tests pass 224/224; Ruff, format, sdist/wheel build, isolated
  wheel install/removal, and the wheel-installed current-main observer smoke
  pass. These checks exercise interface and packaging behavior, not serving.
- With the plugin wheel and Extension Manager at pinned commit `5aa797cdc`
  temporarily installed alongside current core main, `extension check`
  reported `installed`, `discovered`, `compatible`, and `configured`. Its
  evidence explicitly accepted host package version
  `0.29.1.post1.dev522+gc3e05329b.empty` and observer API 1.0. The temporary
  plugin/Manager installs were then removed. Repeating this check after
  uninstalling Ascend produced the same four states; the core-first
  environment also passed `uv pip check` with those tools installed.
- The local `tools/smoke_current_host_observer.py` public-API smoke against
  installed current core and plugin wheel wrote two canonical Worker-local
  transfer events and four separately scoped host facts (requeue, receipt,
  admission, first compute), then unregistered. Descriptor correlation remains
  rejected. These callback objects are synthetic; no connector or model
  executed.
- Live-process rule: ECPA reported `runtime_effective` while the owning process
  was alive and removed that state after the same process stopped.

The CPU/host checks above started no model server, port, child worker, or
accelerator. The separate one-tensor NPU access check below is not among them.

To repeat the synthetic callback smoke with a CPU-capable environment that
already has the host dependencies, run from this repository's root:

```bash
PYTHONPATH="$PWD/src" TORCH_DEVICE_BACKEND_AUTOLOAD=0 \
  VLLM_TARGET_DEVICE=cpu VLLM_PLUGINS='' \
  /root/kv-transfer-observability-workspace/runtime-candidate/.venv/bin/python \
  tools/smoke_current_host_observer.py
```

The command relies on the isolated environment's editable current-main core
build. It constructs public host callback records; it does not run KV copies.

## Fail-closed boundary

The active binding accepts Worker-local `transfer_submitted`,
`transfer_completed`, and `transfer_cancelled` records. It also accepts the
host's `transfer_receipt`, `recovery_requeued`, `recovery_admitted`, and
`first_compute` callbacks as closed, bounded
`vllm-hust.kv-transfer-host-fact.v1` records with explicit
`host_reported_unjoined` scope. They do not satisfy the canonical v2 complete
restore-chain validator. Unknown API versions, malformed identities, rosters,
and field combinations are rejected; runtime callback errors remain fail-open.

The follow-up binding generates a nonzero, per-process incarnation and keeps
only bounded in-flight job associations. A rejected submission creates no
association; a mismatched terminal record cannot close another request's job.
The entry point releases completed H2D receipt state because it cannot safely
join scheduler admissions to Worker restores. This allows long-running
observation of transfer events and host facts without claiming a complete
cross-process recovery chain. A process-local incarnation is not a
host-supplied cross-process generation.

At the 2026-10-08 read-only host main `c3e05329b7`, scheduler requeue and
admission records carry request ID, epoch, and job roster, but no worker rank
or generation. Worker transfer records carry job ID, rank, and request ID but
no recovery epoch. Worker first-compute carries epoch, job roster and rank,
but not the corresponding accepted per-worker transfer receipts. The copy
site emits region-relative descriptors before the worker's submission event
with `rank=None` and no request ID. Job IDs alone do not prove that these
observations came from the same worker incarnation or recovery episode:
scheduler job numbering restarts with a new connector instance. The plugin
therefore keeps those paths detached under its strict per-Worker attribution
model. The existing v1.0 scheduler and first-compute events can be represented
as separate host observations without claiming that stronger join. Proving a
single end-to-end chain across processes would require a verifiable bounded
process/generation and episode association across scheduler, copy, and worker
callbacks, plus exact Worker-incarnation receipt rosters. No timestamp or
filename join is substituted for those facts. The host's current worker
completion path also asserts success, so backend failure handling is not
qualified by a successful CPU callback test.

Ascend `575bd1c265` reuses the core `OffloadingConnectorWorker` through its
native connector. Its V1 model runner overrides `execute_model` and does not
call `observe_forward_batch` before `_model_forward`, so it cannot currently
publish the host's first-compute callback through that path. Its V2 runner
delegates to the core `GPUModelRunner.execute_model`, where the hook exists;
that source relationship is not a runtime receipt. The Ascend native
`cpu_npu.py` copy path has no corresponding region-layout publication.

The plugin now consumes the already-published v1.0 scheduler and first-compute
data as separate typed host facts. The reused job/rank tests demonstrate that
these reports alone cannot prove which Worker incarnation performed a restore.
A stronger per-Worker cross-process join would need the following additional
facts:

| Path | Required host fact | Current gap |
|---|---|---|
| Scheduler receipt/admission to Worker restore | Bounded origin/generation and recovery-episode association, with the exact accepted rank roster | Scheduler and Worker callbacks cannot prove the same incarnation and episode |
| Copy descriptor to submitted transfer | Address-free request/rank/origin association and an ordering rule for accepted jobs | CPU copy-site descriptor precedes submission and lacks request/rank; native NPU copy site has no equivalent callback |
| Ascend V1 first forward | Default-off forwarding of the existing `observe_forward_batch` hook at the real forward boundary | V1 override does not call it |
| Backend failure | Terminal result that distinguishes failed transfer from success without changing serving safety | Current core Worker asserts success before publishing completion |

Any expanded host contract needs its own version and focused CPU fixtures
before the plugin consumes the new fields. This is a proposed stronger
attribution requirement, not a claim that #46 failed to complete its agreed
v1.0 scope. The plugin rejects the descriptor callback for lack of request/rank
correlation and rejects malformed or unknown callbacks.

A concrete versioned seam candidate is to issue one bounded scheduler
incarnation ID when the connector is created, carry it through
`OffloadingConnectorMetadata`, and include it in scheduler and Worker
observations. `TransferJob` would carry the request's recovery epoch when the
load belongs to an open recovery episode. Each Worker would issue its own
bounded incarnation ID and echo it in completion metadata. The scheduler's
accepted receipts and admission would report exact
`(job ID, rank, Worker incarnation)` tuples. The admission metadata sent back
to Workers would carry that accepted roster, and the first-compute callback
would carry it with the same scheduler incarnation and epoch. The plugin could
then reject any submission, receipt, admission, or first-compute record whose
origin, episode, or roster differs; a reused numeric job ID after a restart
would remain distinguishable without a clock or filename join. Descriptor
records need the same origin/job/request/rank fields and an explicit ordering
rule before they can be included. The plugin would bind this as a new host API
version, keeping version 1.0's worker-local behavior separate. Required
negative fixtures include reused job IDs across scheduler incarnations, rank
reuse after Worker restart, incomplete receipt rosters, repeated recovery
epochs, and delayed first-compute records. These fields were a proposal at
the published v1 baseline; the uncommitted current-core follow-up above now
implements the recovery identity path.

The following are not qualified:

- cross-process recovery admission and first-compute correlation;
- descriptor capture from core or Ascend copy sites;
- the Ascend V1 runner and `cpu_npu.py` follow-up call sites;
- device transfer correctness, resource release, overhead, or performance.

The target machine exposes an idle, healthy 910B2 and CANN 9.1.0. A separate
Torch 2.13.0 CPU / TorchNPU 2.13.0rc1 process allocated one NPU tensor and
read `[1.0]` back successfully. That is a device-access check, not a KV copy,
model run, plugin activation, or validated runtime. Ascend main
`575bd1c265` records upstream core `ced6857afa0e` as its verified commit,
while the organization core at `c3e05329b7` is a different source revision.
The isolated CPU preflight lacks pinned `triton-ascend==3.6.0`; the checked
PyPI and Huawei Ascend indexes do not publish that version. Overlaying the
two current source trees on the older CPU environment imports connector and
spec modules. Direct imports of both runners hit an Ascend `DeviceOperator`
initialization cycle. Importing `vllm_ascend.ops` first lets the V1 runner
import, so that cycle is import-order dependent in this CPU preflight, not a
proven NPU startup failure. With the same ordering, V2 fails because current
core no longer has
`vllm.v1.worker.gpu.spec_decode.autoregressive`. Replacing current core with
Ascend's recorded verified commit instead fails V2 on its missing
`prepare_dcp_local_seq_lens` symbol.

For comparison only, the older core #46 head `c2e9e7d531` plus Ascend
`575bd1c265` forms a narrower source-pair candidate. It is not selected under
the current-core-main priority. That older core revision has
both the old spec-decode path and the DCP helper. With `vllm_ascend.ops`
imported first in the same CPU preflight environment, both V1 and V2 runner
modules import. Core observer/recovery tests pass 42/42, Ascend native
CPU-offload tests pass 13/13, the V2 speculator file passes 35/35, and the
synthetic plugin callback smoke records two transfer events and unregisters.
Initially the broader three-file V2 CPU mock suite passed 52/81: 28 cases
constructed speculators without their initializer and omitted upstream
`dcp_size`; one assigned the now read-only `dp_size` property. An isolated
Ascend worktree at the same source commit changes only test fixtures: it
supplies `dcp_size` and the builder's `supports_update_block_table`, derives
`dp_size` from test config, and expects the upstream padded flag count. With
those four test-only corrections, the same suite passes 81/81. The untouched
Ascend source worktree remains at 52/81. Neither the fixed fixtures nor the
passing imports establish model execution, KV transfer, or plugin callbacks
on a device.

Current organization core main and Ascend main were co-installed as editable
non-device builds in the isolated CPU preflight environment. That verified
package identity for source/CPU checks, but the pair failed dependency
resolution: current core requires `transformers>=5.16.1,<5.20.0`, while
Ascend main pins `transformers==5.14.1`; Ascend also pins the unavailable
`triton-ascend==3.6.0`. The installed `transformers==5.19.0` satisfies core
and conflicts with Ascend. Current core also lacks the older
`vllm.v1.worker.gpu.spec_decode.autoregressive` module imported by Ascend V2.
The alternative #46-head source pair passes the isolated import/CPU fixture
checks above but is not the selected current-main baseline or a qualified NPU
runtime. Ascend was then uninstalled from the isolated environment; with
current core still installed, `uv pip check` passes for all 183 packages and
the observer API still imports. The synthetic six-record callback smoke also
passes after Ascend removal. This is a core-first CPU preflight, not an
Ascend compatibility claim.

## Core-first Ascend selection

The selection target is fixed at organization core main `c3e05329b7`
(`vllm 0.29.1.post1.dev522+gc3e05329b.empty`, Torch 2.13.0, Transformers
`>=5.16.1,<5.20.0`). Switching only the Ascend checkout did not reveal a
ready-to-run pair in the candidates inspected on 2026-10-08:

| Ascend candidate | Concrete incompatibility with fixed core main |
|---|---|
| HUST main `575bd1c265` | Pins Transformers 5.14.1 and unavailable Triton Ascend 3.6.0; V2 imports the removed `gpu.spec_decode.autoregressive` path. |
| HUST `feature/latest-main-compat` `b84e4fce` | Retains Transformers 5.14.1, Triton Ascend 3.6.0, and the removed import path. |
| HUST `feat/kv-transfer-observability-call-sites` `3a5912bb` | Adds relevant observation call sites but retains the same dependency and V2 import mismatches. |
| Upstream Ascend `releases/v0.29.0rc` `b3d3bb68` | Pins Transformers 5.14.1 and Torch 2.10.0, while fixed core requires Transformers at least 5.16.1 and Torch 2.13.0. |

Those are source/dependency checks, not an exhaustive search of every remote
branch. The latest HUST main is also still anchored to upstream core
`ced6857afa0e`, rather than this organization's current core main. A
compatible Ascend target therefore needs its own isolated candidate: align
the dependency declarations to core, update the V2 imports and any changed
interfaces, obtain a matching Triton Ascend build, then pass CPU runner and
real NPU KV-transfer checks before advertising it. No plugin Manifest change
is needed to make the fixed core version acceptable. No model service, real
KV transfer, complete cross-process receipt, or plugin NPU observation has
been demonstrated. Correctness and matched overhead measurements remain
pending a runnable Ascend pair and identity-bearing host seam.

These omissions are intentional. A scheduler job id is not treated as a
process-scoped transfer identity, and historical B134 compatibility is not
described as current native NPU validation.
