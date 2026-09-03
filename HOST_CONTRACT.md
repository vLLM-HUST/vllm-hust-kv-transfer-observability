# KV transfer observability host contract

The extracted sinks are host-independent and receive immutable data. A vLLM host
provider must supply these seams before activation:

1. `vllm.kv-transfer.events.v1`: lifecycle events for preserve, transfer start,
   transfer completion, restore, failure, and cancellation.
2. `vllm.kv-transfer.descriptors.v1`: region-relative offsets and sizes only;
   process addresses, device pointers, and KV payloads are forbidden.
3. `vllm.kv-transfer.identity.v1`: stable request, transfer, rank, and process IDs
   with bounded cardinality (field definitions below).
4. `vllm.kv-transfer.observer.v1`: default-off observer registration outside the
   scheduler and connector hot path.

The Extension Manager may configure and validate the sink. It must not read KV
payloads, own transfer lifecycle, or enable filesystem writes without an explicit
operator-provided destination.

## Event ownership boundary (decision 2026-09-03, owner call)

The legacy B134 six-event recovery chain spans two layers. To avoid duplicate
instrumentation, scheduler-layer events are owned by the vLLM host EventBus and
this package's host adapter emits only kv_offload-layer events. Events are
therefore in one of three states:

1. **Implemented host events** (vllm-hust#6, currently UNMERGED):
   `RequestPreempted` and `RequestFinished` (typed, carry `ts_monotonic_ns`).
   `preempt` maps to `RequestPreempted(reason="preempt")`; no timing information
   is lost by not re-emitting it in this adapter.
2. **Proposed host seams** (NOT yet implemented anywhere; they must land in the
   host EventBus before the full chain is observable on the scheduler side):
   `wakeup`, `admission`, `scheduled` (resume-path ordering, per the legacy
   chain contract), and `first_compute` (recovered request's first prefill or
   decode on the worker, legacy core PR #236 semantics — ownership: recovery
   observer / host side, TBD). This document must NOT be read as claiming these
   exist in vllm-hust#6 today.
3. **Adapter-owned events** (implemented by this package's sink; vocabulary
   below, enforced by `EVENT_VOCABULARY_V1` in code).

Event counts: adapter vocabulary = 10; implemented host events = 2;
proposed host seams = 4. Total legacy-inspired event set = 16, of which 10 are
this adapter's responsibility.

## Adapter event vocabulary (v1)

Normative vocabulary for this package's host adapter, extracted from the legacy
B134 instrumentation (vllm-hust core PR #220, author @xiehanlong834-gif; see
`docs/semantic-audit.md`). The sink rejects any event name outside this
vocabulary at runtime (fail-closed, tested). Scheduler-layer events
(`preempt`/`wakeup`/`admission`/`scheduled`) are deliberately absent.

Restore path (per request):

| event | emitted when | normative payload fields |
|---|---|---|
| `restore_start` | load of a preempted request begins | keys |
| `restore_done` | load of a preempted request completes | keys |

Store / eviction evidence:

| event | emitted when | normative payload fields |
|---|---|---|
| `cpu_store` | blocks stored to the CPU tier | duration_us, evicted_keys, stored_keys |
| `cpu_evict` | explicit eviction from CPU tier | duration_us, evicted_keys |
| `evict` | tiering prepare-store eviction | duration_us, keys |

Transfer / worker evidence (per job):

| event | normative payload fields |
|---|---|
| `transfer_submit` | bytes, descriptors, direction, descriptor_us, dependency_us, submit_us |
| `swap_d2h_submit` | descriptors, duration_us |
| `gather_h2d` | dma_runs, duration_us |
| `copy_observed_complete` | bytes, direction, completion_observed_ms, device_event_ms |
| `sched_step` | duration_us |

### Ordering contract

Hard constraints within this adapter's stream (pinned by tests; a host adapter
MUST preserve them):

- Restore data path: `restore_start` -> `restore_done`.
- `cpu_store` emission must be reachable, i.e. appear before any early return in
  the prepare-store path.
- None of this adapter's events may be gated on the vLLM `log_stats` flag:
  observability must be independent of stats logging.

Intended full-chain order for one preempted request (cross-stream; consumers
join adapter stream + host EventBus stream on `request_id`):

`preempt` (host) -> (store evidence: `cpu_store` / `cpu_evict` / `evict`) ->
`restore_start` -> `restore_done` -> `wakeup` (host, proposed) ->
`admission` (host, proposed) -> `scheduled` (host, proposed) ->
`first_compute` (host/worker, proposed).

Scheduler-side ordering (`wakeup` -> `admission` -> `scheduled`, and emits not
gated on `log_stats`) is a host EventBus contract (vllm-hust#6, proposed
extension), to be verified by source-level tests in the host repository.

## Identity.v1 (bounded identity)

The sink payload carries process id (`pid`) implicitly and the following
optional identity fields. Adapters MUST use the dedicated field for each
dimension and MUST NOT encode one identity into another (in particular,
transfer jobs are never encoded into `request_id`):

| field | meaning | type / bound |
|---|---|---|
| `request_id` (required) | the request this event belongs to | non-empty str; bounded by in-flight request ids |
| `transfer_id` | a transfer/job id (per transfer job) | optional non-empty str (e.g. `job-42`) |
| `recovery_epoch` | recovery round of a preempted request | optional int >= 0 |
| `rank` | worker rank emitting the event | optional int >= 0 |
| `generation` | worker/process generation (restart counter) | optional int >= 0 |
| `pid` (always written by the sink) | process id | int |

`first_compute` receipts, once the proposed seam lands, must correlate to the
request via `request_id` and to the preceding transfer via `transfer_id`.

## Timing semantics

- Ascend `swap_blocks_batch` device-event elapsed time is unreliable for phase
  accounting. Host adapters MUST use wall-clock phase durations (`*_us`) and MUST
  keep completion-observation latency distinct from device event time:
  `copy_observed_complete` carries `completion_observed_ms` (host wall clock) and
  `device_event_ms` together.
- When observability is disabled (no sink configured), host code MUST take the
  zero-overhead path: no `time.monotonic()` call, no payload construction, no
  dependency on vLLM stats flags.

## Descriptor layout v1 — status: proposal, not final

Legacy #220 descriptors carried region identity (`src_region` / `dst_region`,
e.g. `src_tensor_0`). The extracted v1 schema keeps region-relative offsets only
and rejects extra fields (fail-closed). The `src_region`/`dst_region` loss is a
real information gap. Two options remain OPEN and MUST be decided before
activation: extend the v1 schema with region ids, or switch to per-region
capture invocations from the host adapter. Until then the descriptor contract
is a proposal, not a final specification; details in `docs/semantic-audit.md`.
