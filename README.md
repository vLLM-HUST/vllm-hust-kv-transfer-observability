# KV Transfer and Recovery Observability

Owner-led migration carrier for identity-bound KV transfer, recovery, descriptor-layout, and first-compute receipts. It observes runtime behavior and does not own KV storage or service lifecycle.

**Status: the typed, bounded, address-free observation core and sinks are
installable and tested; automatic vLLM attachment remains blocked until a real
current-host seam and the remaining graduation evidence exist.**

Technical ownership belongs to @xiehanlong834-gif, @Remygred. Source extraction must preserve exact authorship, license, tests, constraints, and evidence before activation is considered.

See [MAINTAINERS.md](MAINTAINERS.md) and [PROVENANCE.md](PROVENANCE.md).

## Extension framework

Extension ID: `org.vllm-hust.kv-transfer-observability`

This repository follows the vLLM-HUST Extension Template. The current package
is deliberately `import_only`: the observability primitives can be imported and
tested, but Extension Manager must refuse enablement until explicit observer
registration, configuration, and compatibility evidence land.

```bash
python -m pip install "vllm-hust-ext @ git+https://github.com/vLLM-HUST/extension-manager.git@main"
python -m pip install -e ".[test]"
vllm-hust-ext extension inspect org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension check org.vllm-hust.kv-transfer-observability
pytest -q
```

The static Manifest 0.2 descriptor lives inside the Python distribution under
`src/`. Installation alone changes no vLLM behavior.

## Observation core

The current host-independent API accepts immutable typed records rather than
arbitrary event strings or `**fields`. For example:

```python
from vllm_hust_kv_transfer_observability import (
    JsonlKVTransferEventSink,
    KVTransferObservation,
    ObservationEvent,
    ObservationIdentity,
    TransferDirection,
    TransferIdentity,
)

record = KVTransferObservation(
    event=ObservationEvent.TRANSFER_COMPLETED,
    identity=ObservationIdentity(
        request_id="request-1",
        worker_generation=1,
        rank=0,
        recovery_epoch=1,
    ),
    transfer=TransferIdentity("0123456789abcdef0123456789abcdef:t:1"),
    direction=TransferDirection.H2D,
    bytes_moved=4096,
    duration_ns=1000,
)

with JsonlKVTransferEventSink("events.jsonl") as sink:
    accepted = sink.emit(record)
```

`emit()` is non-blocking and returns `False` on a closed/full/invalid path;
runtime observation failures are counted instead of propagating into serving.
Descriptor inventories use the same bounded identity, typed relative regions,
directory confinement, and atomic publication. See
[`docs/observation_core.md`](docs/observation_core.md) for the schemas, bounds,
failure policy, and counters.

The initial host-independent lifecycle normalizer maps the merged legacy core
transfer/recovery callbacks and the core/Ascend first-compute forwarding
semantics into the same canonical records. It remains detached from any host;
see [`docs/normalization.md`](docs/normalization.md).
