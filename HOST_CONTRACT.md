# KV transfer observability host contract

The extracted sinks are host-independent and receive immutable data. A vLLM host
provider must supply these seams before activation:

1. `vllm.kv-transfer.events.v1`: lifecycle events for preserve, transfer start,
   transfer completion, restore, failure, and cancellation.
2. `vllm.kv-transfer.descriptors.v1`: region-relative offsets and sizes only;
   process addresses, device pointers, and KV payloads are forbidden.
3. `vllm.kv-transfer.identity.v1`: stable request, transfer, rank, and process IDs
   with bounded cardinality.
4. `vllm.kv-transfer.observer.v1`: default-off observer registration outside the
   scheduler and connector hot path.

The Extension Manager may configure and validate the sink. It must not read KV
payloads, own transfer lifecycle, or enable filesystem writes without an explicit
operator-provided destination.

## Event ownership boundary (decision 2026-09-03, owner call)

The legacy B134 six-event recovery chain spans two layers. To avoid duplicate
instrumentation, scheduler-layer events are owned by the vLLM host EventBus
(`vllm/v1/events.py`, PR vllm-hust#6), and this package's host adapter emits
only kv_offload-layer events:

- Scheduler layer (owned by host EventBus, NOT emitted by this adapter):
  `preempt` / `wakeup` / `admission` / `scheduled`. The EventBus carries
  `ts_monotonic_ns` on every event, so no timing information is lost.
- kv_offload layer (owned by this adapter): the vocabulary below.
  Full-chain reconstruction is done by consumers joining both streams on
  `request_id` (see Ordering contract).

## Adapter event vocabulary (v1)

Normative vocabulary for this package's host adapter, extracted from the legacy
B134 instrumentation (vllm-hust core PR #220, author @xiehanlong834-gif; see
`docs/semantic-audit.md`). An adapter emitting an event name outside this
vocabulary MUST be rejected by configuration validation. `request_id` carries
either a request id or a transfer job id (`job<N>`, emitted by
transfer/worker evidence).

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
`restore_start` -> `restore_done` -> `wakeup` (host) -> `admission` (host) ->
`scheduled` (host).

Scheduler-side ordering (`wakeup` -> `admission` -> `scheduled`, and emits not
gated on `log_stats`) is a host EventBus contract (PR vllm-hust#6), verified by
source-level tests in the host repository.

## Timing semantics

- Ascend `swap_blocks_batch` device-event elapsed time is unreliable for phase
  accounting. Host adapters MUST use wall-clock phase durations (`*_us`) and MUST
  keep completion-observation latency distinct from device event time:
  `copy_observed_complete` carries `completion_observed_ms` (host wall clock) and
  `device_event_ms` together.
- When observability is disabled (no sink configured), host code MUST take the
  zero-overhead path: no `time.monotonic()` call, no payload construction, no
  dependency on vLLM stats flags.

## Descriptor layout v1 boundaries (open question)

Legacy #220 descriptors carried region identity (`src_region` / `dst_region`,
e.g. `src_tensor_0`). The extracted v1 schema keeps region-relative offsets only
and rejects extra fields (fail-closed). Region identity must be re-attached
either by per-region capture invocations from the host adapter or by extending
the v1 schema with region ids. This decision must be settled before activation;
details in `docs/semantic-audit.md`.
