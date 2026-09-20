# Minimal current-host seam for KV lifecycle observations

Status: proposal for maintainer review; not an implemented or accepted host API.

This document narrows the host work required by Issue #2 after checking the
current host implementations. It does not activate the plugin, patch a host,
or claim runtime compatibility. Names used below describe required semantics;
the host maintainers retain ownership of the final public module and API names.

## Audited revisions

Audit snapshot: 2026-09-11. These revisions describe inspected code, not a
promise that moving host branches expose a compatible API.

| Component | Revision |
|---|---|
| `vLLM-HUST/vllm-hust` `main` | `6cdc0304a8bac6f0275a7aa90493226aa38d83f4` |
| `vLLM-HUST/vllm-ascend-hust` `main` | `74f0c0a272376412b51e1c1864803d5f3a0f1b5f` |
| `vLLM-HUST/extension-manager` `main` | `cf1ea71e3e2cb81ab06267ef05eddb3e580ea20b` |
| plugin `main` after PR #5 | `ec3446d936b6ac148e0be33b1dba831f9ecfc0c4` |

The audit was refreshed after the core and Ascend upstream synchronizations.
It also considered open core PRs #3 and #6. Neither is merged or a KV transfer
API. Both currently add their own `vllm/v1/events.py`; current core `main` does
not contain that module, and this proposal must not be implemented as a third
competing event bus.

## Current facts and missing observations

Current core has a general-plugin loader and useful connector-internal facts,
but no public KV lifecycle observer registration:

- `offloading/common.py::TransferJob` associates a scheduler job ID with a
  request and the source/destination specifications;
- `offloading/scheduler.py::_generate_job_id`, `_build_store_jobs`,
  `build_connector_meta`, `update_connector_output`, `request_finished`, and
  `shutdown` own scheduler-side job and request transitions;
- `offloading/worker.py::start_kv_transfers`, `prepare_store_kv`, `get_finished`,
  and `shutdown` own Worker-side submission, completion, and cleanup;
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

Current Ascend has real connector and model execution paths, including
`NPUModelRunner.execute_model`, but no recovery-aware first-compute callback.
Connector-specific callbacks and raw memory registration remain implementation
details and are not safe common observer interfaces.

Extension Manager can render launch configuration and native-manifest
environment, but current core does not consume those native manifests. It also
does not currently select this plugin through `VLLM_PLUGINS`. The plugin must
therefore remain `import_only`; generic plugin discovery alone is not proof of
activation.

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

The plugin-side review on 2026-09-18 recommends core `OffloadingConnector`
D2H/H2D CPU offload as the first integration target, using the existing
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
   `prepare_store_kv`, the Worker invokes the observer. The adapter allocates
   its process-scoped transfer identity and correlates it with the connector
   job ID.
3. `get_finished` invokes completion or failure observation using the same job
   association. A successful H2D completion produces a bounded, address-free
   receipt that can travel through existing Worker-to-scheduler metadata.
4. `request_finished`, Worker-generation replacement, and `shutdown` identify
   still-open attempts as cancellation; they do not fabricate successful
   completion.

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

## Proposed attachment points

These are review locations, not requested patches:

| Host location | Observation available there |
|---|---|
| core `OffloadingConnectorScheduler.build_connector_meta` and job construction | request/job/operation context before Worker handoff |
| core Worker `start_kv_transfers` and `prepare_store_kv` | actual backend submission result |
| core Worker `get_finished` | completion/failure, measured bytes/time, H2D receipt creation |
| core scheduler `update_connector_output` | receipt aggregation, requeue, and exact recovery admission |
| core `request_finished` and scheduler/Worker `shutdown` | explicit cancellation of remaining attempts |
| core and Ascend model runner immediately before real model forward | consume one admitted first-compute context |

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

Maintainer review is needed only for the host-owned interface and call sites.
Before a host change is proposed, reviewers should confirm:

1. whether the shared EventBus work will provide the registry;
2. which current connector path is the supported initial target;
3. where the bounded scheduler-to-Worker context and Worker receipt belong;
4. the exact first-compute call in core and Ascend;
5. that the disabled path has no timestamp, payload, or callback work;
6. that failures never change scheduler, connector, Worker, or model behavior.

After that decision, the next deliverable is the smallest default-off host seam
plus plugin-side fixture/CPU integration tests. Real activation, NPU claims,
compatibility claims, and performance testing remain out of scope until that
integration passes.
