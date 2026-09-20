# KV transfer observability host contract proposal

The extracted sinks are host-independent and receive immutable data. A vLLM host
provider must supply these seams before activation:

1. `vllm.kv-transfer.events.v2`: typed lifecycle events for preserve, transfer start,
   transfer completion, restore, failure, and cancellation.
2. `vllm.kv-transfer.descriptors.v2`: bounded numeric source/destination region
   IDs plus typed region-relative offsets and sizes; process addresses, device
   pointers, arbitrary region names, and KV payloads are forbidden.
3. `vllm.kv-transfer.identity.v1`: stable request, transfer, rank, and process IDs
   with bounded cardinality.
4. `vllm.kv-transfer.observer.v1`: default-off observer registration outside the
   scheduler and connector hot path.

The Extension Manager may configure and validate the sink. It must not read KV
payloads, own transfer lifecycle, or enable filesystem writes without an explicit
operator-provided destination.

## Event ownership boundary (decision record, 2026-09-03; restated on the merged base)

The legacy B134 instrumentation spans two layers. To avoid duplicate
instrumentation and a competing event bus, the layers have different owners and
consumers join them on request identity:

- **Scheduler-layer transitions** (preemption, resume, admission, scheduled) are
  owned by the host's process-local typed event bus work, not by this package's
  host seam. Status: `vLLM-HUST/vllm-hust#6` proposes a default-off typed bus and
  in its unmerged proposal exposes only `RequestPreempted` and `RequestFinished`.
  This is not a claim that current host `main` implements the bus. The resume-side
  events are proposed, not implemented, and must not be read as available.
- **KV-transfer-layer records** (transfer submit/completion/cancellation, recovery
  requeue/admission, first-compute receipt) are owned by this package's host seam
  and already exist as canonical typed records in plugin `main`; the host call
  sites and real event production are not implemented by those records.
- Neither layer may be implemented as a third competing event bus, and no host
  seam may require the host to import plugin classes.

Event counts are defined once, in code, by `b134.py`: `B134_EVENT_COUNT = 14`
(4 scheduler-owned + 10 KV-offload-owned legacy source events) and
`UNIFIED_SOURCE_EVENT_COUNT_WITH_FIRST_COMPUTE = 15`. `RequestFinished` is an
additional host event and is deliberately not part of that source set.

Two legacy requirements remain host-side obligations, not yet covered by any
merged implementation:

- **Observability must not be gated on the vLLM `log_stats` flag.** The legacy
  instrumentation kept event emission independent of stats logging, and
  `wakeup` -> `admission` -> `scheduled` ordering is a scheduler-side contract.
- **Disabled means zero overhead.** With no observer registered, host code must
  take a path with no timestamp read, no payload construction, and no callback
  work — matching the single disabled guard required by the observer seam.

Per-item evidence against the legacy patches is in `docs/semantic-audit.md`.
