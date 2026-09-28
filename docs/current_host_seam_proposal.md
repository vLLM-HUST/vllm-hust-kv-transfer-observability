# Minimal current-host seam for KV lifecycle observations

Status: proposal for maintainer review; not an implemented or accepted host API.

This document narrows the host work required by Issue #2 after checking the
current host implementations. It does not activate the plugin, patch a host,
or claim runtime compatibility. Names used below describe required semantics;
the host maintainers retain ownership of the final public module and API names.

## Audited revisions

Audit snapshot: 2026-09-28. These revisions describe inspected code, not a
promise that moving host branches expose a compatible API.

| Component | Revision |
|---|---|
| `vLLM-HUST/vllm-hust` `main` | `c7b61770097b5c6e9ae11d96baef59e3689231f1` |
| `vLLM-HUST/vllm-ascend-hust` `main` | `17f68177472b896facf3f0501d424d24e16e699a` |
| `vLLM-HUST/extension-manager` `main` | `701aa95ae8d5b23b5ea8c8ec475ceaa470393e54` |
| plugin `main` after PR #6 | `53e69013dcf27fd08a93d142288c93c701edabd3` |

The audit was refreshed after the core and Ascend upstream synchronizations.
It also considered open core PRs #3 and #6. Neither is merged or a KV transfer
API. Both currently add their own `vllm/v1/events.py`; current core `main` does
not contain that module, and this proposal must not be implemented as a third
competing event bus.

Concrete implementation tracking:
[core #43](https://github.com/vLLM-HUST/vllm-hust/issues/43), linked to
[plugin #2](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/issues/2).

Core PR #6 head `12877940b661e0718e023f9649e6466630a5b929` publishes
request finished/preempted/reclaimed events, not transfer receipts. PR #3 head
`ede30a2c9a9e434d283139af9a191f323b48067a` concerns sampled-output movement,
not KV movement. Reuse the common outlet when implemented; neither event family
can be renamed to stand in for the missing KV observations.

## Current facts and missing observations

Current core has a general-plugin loader and useful connector-internal facts,
but no public KV lifecycle observer registration:

- `offloading/common.py::TransferJob` associates a scheduler job ID with a
  request and the source/destination specifications;
- `offloading/scheduler.py::_generate_job_id`, `_build_store_jobs`,
  `build_connector_meta`, `update_connector_output`, `request_finished`, and
  `shutdown` own scheduler-side job and request transitions;
- `offloading/worker.py::start_kv_transfers` and `handle_preemptions` perform
  backend submissions; `prepare_store_kv` queues stores for a later step;
- `get_finished` and `shutdown` own Worker completion and cleanup;
- `vllm/v1/kv_offload/base.py::TransferResult` exposes job ID, success, and
  optional transfer size/time internally.

Current core also has opt-in self-describing offloading cache events in
`offloading/events.py`. `OffloadingEventsTracker` translates internal
offloading residency changes into `BlockStored` and `BlockRemoved` records.
Those records are intended for cache-residency consumers and may contain block
hashes and token IDs. They do not publish transfer submission/completion,
recovery epoch, Worker generation, H2D receipt, or first-compute identity, so
they cannot be relabelled or consumed as this plugin's address-free lifecycle
seam.

These facts are not an external ABI. Worker completion still asserts success,
and the public structures do not carry a recovery epoch, Worker generation,
H2D receipt, or exact first-compute roster. Existing `BlockStored`,
`BlockRemoved`, and `AllBlocksCleared` records describe residency and must not
be relabelled as transfer lifecycle events.

Current Ascend registers `AscendOffloadingConnector` under `OffloadingConnector`
and maps `CPUOffloadingSpec`/`TieringOffloadingSpec` to NPU implementations.
`native/offloading_connector.py` adapts packed/split KV layouts while reusing
core scheduler and Worker transfer logic. `native/cpu_npu.py` issues real DMA
and reports completion after querying its end event. It exposes no sanitized
descriptor callback or recovery-aware first-compute observer.

Ascend now defaults to V2 for eligible configurations via `mrv2_utils.py`.
`worker/v2/model_runner.py::execute_model` delegates to core V2; a proposal
targeting only Ascend `model_runner_v1.py` misses that path.

The two audited main heads are not a compatible runtime pair: Ascend's V1
runner still imports `RoutedExpertsLists` and `RoutedExpertsTensors` from
`vllm.v1.outputs`, which core no longer exports. `worker/worker.py` imports
that runner at module scope even when V2 is selected. This source-level
failure matches [Ascend #34](https://github.com/vLLM-HUST/vllm-ascend-hust/issues/34).
No working NPU pairing is claimed by this audit.

The selected next candidate follows Ascend's repository-recorded upstream core
revision, not an arbitrary rollback. See
[current_host_compatibility.md](current_host_compatibility.md) for the exact
pair, limited source/dependency checks and remaining runtime validation.

Extension Manager can render launch configuration and native-manifest
environment, but current core does not consume those native manifests. It also
does not currently select this plugin through `VLLM_PLUGINS`. The plugin must
therefore remain `import_only`; generic plugin discovery alone is not proof of
activation.

Core now also records general-plugin discovery/resolution/invocation in
`vllm/plugins/evidence.py`, including process identity. This can support
worker coverage checks, but is not a KV event registry or an H2D receipt.
The plugin still has only an Extension Manager bundle-discovery entry point;
there is no `vllm.general_plugins` startup binding. Manager's current vLLM
provider detects policy protocols, not the proposed KV observer protocol.
Real launch integration must bridge these gaps explicitly rather than treat
`inspect_only` plans or empty enabled sets as successful activation.

## Required semantic payload

The host seam needs only enough data for the existing plugin adapter to build
the closed source records below. The host must not import plugin classes. A
host-owned immutable event union carrying equivalent primitive fields is
sufficient.

| Plugin adapter input | Required host facts | Current status |
|---|---|---|
| `CoreTransferSubmitted` | request ID, connector job ID, D2H/H2D operation, block count, monotonic observation point | split across scheduler metadata and Worker submission result |
| `CoreTransferCompleted` | connector job ID, success, observed time, bytes moved, optional device duration, H2D receipt association | job result exists; failure is asserted and receipt is absent |
| `CoreTransferCancelled` | connector job ID, observed time, one closed cancellation reason | no published event |
| `CoreRecoveryRequeued` | request ID, positive recovery epoch, rank, generation, closed requeue reason, observed time | no published recovery observation |
| `CoreRecoveryAdmitted` | the same request identity and the exact sorted successful-H2D transfer roster | no published recovery observation |
| `FirstComputeObserved` | admitted identity, exact transfer roster, receipt ID, prefill/decode kind, observation immediately before real model forward | absent from current core and Ascend |

The adapter already enforces bounded request, transfer, recovery, rank,
generation, receipt, roster, and numeric fields. Invalid host data is dropped
and counted without affecting serving.

## Minimal host behavior

The initial implementation target is core `OffloadingConnector` D2H/H2D CPU
offload, with Ascend's native adapter for NPU, using the existing
connector metadata and Worker-to-scheduler output channels for identity and
receipt handoff. These are proposed integration choices, not verified host
support. Historical Ascend B134 transfer behavior does not by itself prove a
working transfer path or first-compute receipt on current Ascend hardware.

### Registration and dispatch

The host should expose one process-local typed observer registry, preferably as
part of whichever common EventBus design is accepted for core PRs #3/#6:

- explicit `register` returning a removable handle and idempotent `unregister`;
- no observer registered by installation alone;
- one cheap disabled guard before timestamp or payload construction;
- immutable, host-owned event objects with a closed field set;
- a snapshot of registered observers before dispatch, without holding a
  registry lock while calling them;
- observer exceptions isolated from serving, with the failing observer removed
  or disabled;
- no filesystem I/O, waiting, unbounded queueing, KV copy, or payload retention
  in the host callback.

This proposal does not require the host EventBus to serialize plugin records.
The out-of-tree adapter performs validation, normalization, bounded queueing,
and JSONL/descriptor publication.

The intended binding is one sink on the shared host bus, not a separate
registry. The core PR #6 author recommends its `register_sink` shape, but its
request-finished/preempted events alone do not supply KV transfer, recovery or
first-compute observations. Resume-side scheduler events remain a separate
gap. The plugin's `register`/`unregister` protocol wraps the eventual host API;
it does not dictate that API's method names.

### Transfer identity handoff

The current scheduler job ID is necessary but not sufficient. The minimal
handoff is:

1. Scheduler metadata carries request ID, connector job ID, operation,
   recovery epoch when applicable, and a bounded logical-block count to the
   Worker. It must not carry addresses or KV contents.
2. After a backend submission actually succeeds in `start_kv_transfers` or
   `handle_preemptions`, the Worker invokes the observer. `prepare_store_kv`
   alone is not a submission. The adapter allocates
   its process-scoped transfer identity and correlates it with the connector
   job ID.
3. `get_finished` invokes completion or failure observation using the same job
   association. A successful H2D completion produces a bounded, address-free
   receipt that can travel through existing Worker-to-scheduler metadata.
4. Cancellation is emitted only when an attempt is actually abandoned.
   `request_finished` alone is insufficient: stores may legitimately outlive
   the request. Observe shutdown/reset/generation replacement without changing
   the host's draining or block-reclamation behavior. A vanished process is
   incomplete evidence, not a fabricated callback or successful completion.

Use `TransferJob`/`OffloadingConnectorMetadata` for scheduler-to-Worker context
and `OffloadingWorkerMetadata` for receipt return. Current `completed_jobs`
aggregates counts, not rank/generation-specific acknowledgements. Add bounded
observation identity alongside this channel without changing its scheduling
semantics. Preserve each worker's actual submission and successful H2D receipt;
non-writer store acknowledgements in replicated layouts are not physical D2H
transfers. Do not derive worker coverage from a count alone.

This preserves connector ownership of job scheduling and data movement while
allowing the plugin to own only observation identity and correlation.

### Recovery and first compute

A first-compute record is valid only for a recovery episode, not for every
model forward:

1. Scheduler-side recovery uses a positive epoch tied to the request's actual
   preemption/recovery generation.
2. Admission is observable only after the exact successful-H2D receipt roster
   is known. A requeue reports one closed reason instead of being reported as
   admission.
3. The admitted identity and exact sorted roster are forwarded as bounded
   metadata to the Worker.
4. Core and Ascend invoke the same optional observation immediately before the
   first real prefill/decode model forward for that admitted episode, then
   consume it exactly once.
5. Missing, duplicate, stale-generation, or roster-mismatched metadata yields
   no valid first-compute evidence and must not change execution.

If the current host does not implement a recovery episode with these facts,
that capability remains unsupported. The adapter must not infer an epoch or
roster from filenames, timestamps, request proximity, or residency events.

A normal prefix-cache H2D load is not automatically a post-preemption recovery
episode. Use the actual request transition and generation, not a nonzero
transfer count, to classify recovery. Completion makes a request eligible;
admission must correspond to its actual scheduling for execution.

For V1, inspect the real `_model_forward` calls in core `gpu_model_runner.py`
and Ascend `model_runner_v1.py`, after connector preparation. For V2, inspect
core `gpu/model_runner.py` immediately before eager execution, piecewise graph
execution, or full graph replay; Ascend delegates to this runner. Neither
entry to `execute_model` nor `ActiveKVConnector.pre_forward` proves compute:
`no_forward` also calls `pre_forward`, and dummy/profile/capture paths need
exclusion. Issue a receipt only for real scheduled requests with matching
admitted metadata, not padded requests or every batch invocation.

Current asynchronous loads may be submitted after the current batch's forward.
Their first-compute association belongs to a later resumed batch, never to the
batch that happened to enqueue the load.

### Descriptor boundary

The host may expose descriptor layout only after converting implementation
details to the plugin's address-free v2 inventory:

- bounded numeric source/destination region IDs;
- region-relative offsets, size, and D2H/H2D direction;
- request/transfer/job correlation through bounded identity;
- no process address, device pointer, tensor object, token IDs, block payload,
  or arbitrary metadata.

Sanitization happens before data crosses the callback boundary. The plugin
must never receive raw connector descriptors and then attempt to remove
addresses after the fact.

Ascend's `SingleDirectionNPUOffloadingHandler.transfer_async` builds raw
`batch_src`/`batch_dst` pointer arrays for DMA. Do not export those arrays or
retain their tensors. Build bounded region IDs and relative offsets on the
host side before callback dispatch. Plugin callbacks only enqueue immutable
inventories; serialization, atomic publication and fsync belong to a bounded
background writer, not the connector hot path.

## Proposed attachment points

These are concrete locations for the minimal host change, not an existing ABI:

| Host location | Observation available there |
|---|---|
| core `OffloadingConnectorScheduler.build_connector_meta` and job construction | request/job/operation context before Worker handoff |
| core Worker `start_kv_transfers` and `handle_preemptions` | actual backend submission result, excluding non-writer acknowledgements |
| core Worker `get_finished` | completion/failure, measured bytes/time, H2D receipt creation |
| core scheduler `update_connector_output` | per-worker receipt aggregation; completion eligibility, not yet admission |
| core scheduler remote-KV promotion and actual scheduling | recovery/requeue and admission tied to receipt roster |
| actual abandonment/reset and scheduler/Worker `shutdown` | cancellation only where the host abandons an attempt |
| V1 real forward / V2 eager, piecewise and fullgraph execution | consume one admitted first-compute context, excluding no-forward/dummy/profile |

The final call sites should be the narrowest shared paths that cover the fixed
compatibility target. Connector-specific hooks should be used only when a
common path cannot provide the required fact.

## Adapter and activation boundary

Once a host seam is accepted, the out-of-tree plugin adapter will:

- register only after explicit validated enablement and unregister on disable
  or shutdown;
- translate host-owned records to the existing `CoreTransfer*`,
  `CoreRecovery*`, and `FirstComputeObserved` types;
- pass accepted canonical records to the existing bounded sink and descriptor
  capture;
- become inert after unregister and keep serving fail-open on all observation
  errors.

Extension Manager continues to own launch state. The adapter must not modify
Extension Manager's lifecycle model, start a service, or rely on the current
general-plugin behavior that loads every discovered plugin when
`VLLM_PLUGINS` is unset.

## Review and exit criteria

Follow ordinary host review for the minimal interface/call-site change. The
technical review should check:

1. whether the shared EventBus work will provide the registry;
2. which current connector path is the supported initial target;
3. where the bounded scheduler-to-Worker context and Worker receipt belong;
4. the exact first-compute call in core and Ascend;
5. that the disabled path has no timestamp, payload, or callback work;
6. that failures never change scheduler, connector, Worker, or model behavior.

Implement the concrete host gap through the linked host work item while
plugin-side work proceeds independently. No separate approval record is needed.
After source binding and a compatible version pair exist, validate Manager
discover/check/plan/run on exact commits, target-worker coverage, event order
and loss handling, disable/degradation/rollback/uninstall and exit cleanup.
Keep the public manifest `import_only` until graduation; report real execution
and diagnostic overhead rather than claim speedup from fixtures. Provide PRs,
CI, commands and raw receipts before requesting Workshop restoration.
