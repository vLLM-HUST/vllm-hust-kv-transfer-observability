# Issue #2 migration inventory

This document is the Phase 0/1 source-to-destination checklist for Issue #2.
It records observed repository facts and the work explicitly requested by the
issue. It does not add an approval gate, activate the plugin, or claim host or
hardware compatibility.

Audit date: 2026-09-02 UTC.

## Audited revisions

| Role | Revision |
|---|---|
| plugin bootstrap | `70d1fdc05c7a56e29b452aa11ee00f701749cb94` |
| current `vllm-hust/main` | `5bfd76372d66507d014895376458fd507e124e66` |
| current `vllm-ascend-hust/main` | `40f9834ee82aadfa4656ec65e5bd84f4d6241b5f` |
| Extension Manager | `9fb467447e95d753f7002b28575d6802f4347181` |
| legacy core #236 head | `e6cd22e1a915aedb3a1204cf085fa017557abdf9` |
| legacy Ascend #216 head | `66ded6084db0ff8fb58fa288dcbfecce54798bdb` |

## Phase 0 baseline and reproduced gaps

The unchanged bootstrap passes Ruff, format checking, three unit tests, source
and wheel build, Extension Manager discovery/check, and expected rejection of
enablement while its implementation remains `import_only`.

Focused local probes reproduced these gaps without changing implementation:

| Boundary | Reproduced behavior | Required later test/fix |
|---|---|---|
| event name/fields | accepts an arbitrary event and address/payload-like fields | closed event and field schema; reject forbidden/unknown fields |
| event bound | serializes a record larger than 1 MB | explicit string, cardinality, and record-size bounds |
| event write | a short write is accepted; runtime I/O failure propagates | complete-write handling plus fail-open runtime error/drop accounting |
| descriptor job ID | accepts `bool`; a crafted string can escape the capture directory | strict bounded integer identity and resolved-path confinement |
| descriptor write | a short write raises but leaves a partial published file | temporary write, cleanup, and atomic publication |
| descriptor filesystem | symlink/conflict and concurrent publication are not covered | negative and concurrency tests |
| identity | events expose request ID/PID/time; descriptors expose job ID/direction | typed bounded request, transfer, epoch, rank, generation, and receipt identity |
| compatibility | manifest declares host `>=0` and proposed protocols | replace with tested versions and reject unsupported combinations |
| CI | template is `.github/extension-ci.yml` | permissioned workflow must ultimately live under `.github/workflows/` |

These are implementation inputs for later phases, not evidence that the
plugin is ready to attach to a service.

## Current-host seam audit

The four APIs in `HOST_CONTRACT.md` are proposals, not published host APIs.
The current host revisions do not contain `kv_recovery_profile.py`, the old
`observe_kv_recovery_first_compute` callback, or an equivalent named observer
registration seam.

Current `vllm-hust` does expose:

- generic `vllm.general_plugins` loading;
- internal OffloadingConnector scheduler/worker methods such as
  `build_connector_meta`, `start_kv_transfers`, `get_finished`, and
  `request_finished`;
- scheduler-assigned integer job IDs and completion metadata;
- aggregate transfer metrics and KV cache events.

Generic plugin loading does not by itself deliver typed transfer/recovery/
first-compute callbacks. The OffloadingConnector methods are execution-owned
internal paths, not a verified observer ABI. Therefore the plugin cannot claim
real attachment at these revisions. Phase 4 must first verify a supported
current extensibility route; if none supplies a required observation, the
separate host change must be limited to the missing default-off callback.

Current `vllm-ascend-hust` runs model forward from
`NPUModelRunner.execute_model`, but no longer contains the optional helper from
legacy PR #216. Model execution remains host-owned; the plugin must not patch or
wrap it silently at installation time.

## Source ownership and migration mapping

| Legacy source | Relevant behavior | Classification | Planned plugin destination or treatment |
|---|---|---|---|
| core #220 `vllm/v1/b134_events.py` | JSONL event emission | plugin-owned seed | replace open strings/`**fields` with typed records in `events.py`; add safe bounded writer behavior |
| core #220 `vllm/v1/kv_offload/cpu/b134_descriptor_layout.py` | address-free relative descriptor capture | plugin-owned seed | harden `descriptors.py` with identity/path/inventory bounds and atomic publication |
| core #220 scheduler/CPU/NPU/tiering edits | event timing and call sites | host-owned observations | use only to identify event meaning; do not copy execution paths into the plugin |
| core #220 tests | event order and runtime fixtures | reusable test semantics | rewrite host-independent cases under plugin tests; use fixtures for adapter tests |
| core #236 `vllm/v1/kv_recovery_profile.py` | bounded identities, transfer/wait/first-compute receipts, observer interfaces/state | mixed | migrate typed identities, normalization, bounded correlation, and accounting into plugin modules; keep host callback invocation as a seam |
| core #236 OffloadingConnector scheduler/worker/common edits | create jobs, start/finish transfers, invalidate, requeue, aggregate receipts | host-owned execution plus narrow observations | represent callback inputs in adapters; do not move scheduling, storage, copy, wait, or connector policy |
| core #236 GPU connector/model-runner edits | invoke first-compute observation | host-owned seam | consume a supported callback only; request a minimal seam if current host has none |
| core #236 tests | identity, capacity, ordering, invalidation, disabled-path behavior | reusable semantics | convert to host-independent core tests and current-host adapter fixtures |
| Ascend #216 `worker/kv_recovery.py` | optional first-compute compatibility helper | host-owned seam precedent | use as semantic source for adapter/fixture behavior; do not make the plugin own model forward |
| Ascend #216 `model_runner_v1.py` | call before real model forward | host-owned execution point | must remain in Ascend host if a seam is needed |
| Ascend #216 compatibility tests | callable/missing/non-callable hook behavior | reusable adapter semantics | reproduce as plugin adapter negative/compatibility fixtures |
| archive `c5f82...` benchmark patch | guarded historical benchmark publication | unrelated | exclude |
| historical approval/attestation prose | former process scaffolding | obsolete/non-normative | exclude |

`events.py` and `descriptors.py` already contain bootstrap extractions from
core #220, but the reproduced gaps above mean neither is yet a safe finished
migration.

### File-level source inventory

The relevant core #217/#220 files are:

- plugin-owned seeds: `vllm/v1/b134_events.py` and
  `vllm/v1/kv_offload/cpu/b134_descriptor_layout.py`;
- host-owned call sites: `vllm/v1/core/sched/scheduler.py`,
  `vllm/v1/kv_offload/cpu/manager.py`,
  `vllm/v1/kv_offload/cpu/npu_worker.py`, and
  `vllm/v1/kv_offload/tiering/manager.py`;
- reusable test semantics: `tests/v1/test_b134_events.py`,
  `test_b134_descriptor_layout.py`, `test_b134_chain_contract.py`,
  `test_b134_runtime_paths.py`, and `test_b134_scheduler_restore.py`.

The relevant core #221/#236 files are:

- mixed observer/core source: `vllm/v1/kv_recovery_profile.py`;
- host-owned connector and scheduling paths:
  `vllm/distributed/kv_transfer/kv_connector/v1/base.py`,
  `offloading/common.py`, `offloading/scheduler.py`, `offloading/worker.py`,
  `offloading_connector.py`, and `vllm/v1/core/sched/scheduler.py`;
- host-owned first-compute paths: `vllm/v1/worker/gpu/kv_connector.py`,
  `vllm/v1/worker/gpu/model_runner.py`,
  `vllm/v1/worker/gpu_model_runner.py`, and
  `vllm/v1/worker/kv_connector_model_runner_mixin.py`;
- reusable test semantics under
  `tests/v1/kv_connector/unit/offloading_connector/`,
  `tests/v1/test_kv_recovery_profile.py`, and
  `tests/v1/worker/test_kv_recovery_forward_observer.py`;
- repository tooling only: `tools/pre_commit/check_forbidden_imports.py`.

The relevant Ascend #216 files are
`vllm_ascend/worker/model_runner_v1.py`,
`vllm_ascend/worker/kv_recovery.py`, and
`tests/ut/worker/test_kv_recovery_compat.py`.

## Issue #2 requirement-to-deliverable map

The following destinations are a review checklist, not a promise that absent
files already exist.

| Issue requirement | Intended repository area | Evidence needed before completion |
|---|---|---|
| exact provenance and migratable subset | `PROVENANCE.md`, this inventory | authors/licenses/states/commits verified; unrelated and duplicate history identified |
| real vLLM host adapter/entry point | future `adapters/` and explicit entry point | current supported seam or a separately reviewed minimal host callback; disabled path inert |
| real event/descriptor connection | typed core plus adapters | fixture integration, then real service smoke |
| bounded lifecycle identity | typed core schema/normalizer | positive, boundary, invalid, correlation, and overflow tests |
| fixed compatibility matrix | compatibility module and docs | exact tested Python/manager/vLLM/Ascend/hardware/model/features |
| closed configuration/lifecycle | configuration and lifecycle modules/docs | unknown/conflict/dependency/disable/rollback/uninstall tests |
| real correctness/performance evidence | benchmark/evidence tooling and result records | exact commands/environment/commits/raw repetitions on target hardware |
| executable operator documentation | installation and operations docs | clean-environment reproduction without workspace-specific paths |
| actual CI workflow | `.github/workflows/ci.yml` | maintainer with workflow permission installs it; normal checks pass |

## Phase 1 exit check

- Core #217/#220/#221/#236 and Ascend #216 are inventoried with exact heads,
  merge state, authors, license, files, and semantic roles.
- The #220 merge-parent extraction error is documented and excluded.
- The #221/#236 duplicate lineages are documented using stable patch IDs.
- Every behavior considered for migration has a plugin-owned, host-owned,
  reusable-test, obsolete, or unrelated classification.
- Current host revisions have been checked independently; historical merge
  state is not treated as current compatibility.
- No legacy patch has been bulk-applied, no runtime code has been changed, and
  the manifest remains `import_only`.

## Phase 2 local implementation status

The host-independent Phase 2 core now implements the mapped schema and sink
work in `schema.py`, `events.py`, and `descriptors.py`. Focused tests cover all
closed event shapes, identity and association bounds, unknown/mismatched input,
record/queue/file capacity, short writes, runtime destination failure,
symlinks, directory replacement, conflicts, partial-write cleanup, atomic
publication, and concurrent emit/capture/close.

This status does not satisfy the later host-adapter, configuration lifecycle,
compatibility matrix, real hardware, performance, CI-permission, or activation
requirements. The manifest remains `import_only`.
