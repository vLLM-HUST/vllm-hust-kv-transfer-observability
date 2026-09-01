# KV transfer observability host contract proposal

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
