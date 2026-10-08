# KV Transfer and Recovery Observability

Bounded, address-free observation of vLLM-HUST offloading transfers. The
extension observes runtime behavior; it does not own KV storage, devices,
external services, or the vLLM process lifecycle.

Technical ownership belongs to @xiehanlong834-gif and @Remygred. See
[MAINTAINERS.md](MAINTAINERS.md) and [PROVENANCE.md](PROVENANCE.md).

## Current status

The Manifest 0.3 carrier is activatable against the versioned
`vllm.kv-transfer.observer` 1.x host protocol. Its active scope is deliberately
narrow:

- the `vllm.general_plugins` entry point attaches only after explicit ECPA or
  `VLLM_PLUGINS` intent;
- worker-local submit, complete, and cancel callbacks are normalized and sent
  to a bounded asynchronous JSONL sink;
- host receipt, recovery requeue/admission, and first-compute callbacks are
  saved as closed, bounded `host_reported_unjoined` facts in the same sink;
- the uncommitted current-core v2 seam additionally reports scheduler/Worker
  generations, recovery epochs, and exact accepted rosters; the plugin writes
  these as bounded correlated records for offline chain validation;
- the host emits `runtime_effective` evidence only after the plugin callback
  returns `True` for a real transfer record;
- stop unregisters the observer and closes plugin-owned writer threads.

The published v1-only carrier cannot prove a cross-process recovery roster.
The local v2 follow-up adds that proof path. A single-card 910B2 diagnostic
also observed real CPU KV offload and restore, including two complete
preemption/recovery episodes under pressure. This is a narrow experimental
pairing, not a qualified Ascend installation or compatibility matrix.
Descriptor capture, broader device correctness, failure handling, and
performance remain separate gates. Successful
installation, enable intent, runtime observation, and performance evidence are
four different states.

## ECPA lifecycle

```bash
python -m pip install "vllm-hust-ext @ git+https://github.com/vLLM-HUST/extension-manager.git@5aa797cdc80d2d628b8f0e1548c53ff484df534b"
python -m pip install .

vllm-hust-ext extension inspect org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension check org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension enable org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension plan org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension render org.vllm-hust.kv-transfer-observability
```

`check` fails closed unless the installed host publishes
`vllm.kv-transfer.observer` in its capability registry. Enabling records intent;
it does not manufacture compatibility or runtime-effective evidence.

By default each process writes to a launch- and PID-scoped file under the
system temporary directory. Set
`VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVENT_PATH` through the extension's saved
environment configuration to select an existing writable parent directory.

## Failure and identity boundaries

The host callback never serializes or performs file I/O. The plugin uses
process-scoped transfer identifiers, treats process restart as a new identity,
and drops unknown, malformed, out-of-order, or unsupported records. Queue,
capacity, serialization, and filesystem failures are counted and do not escape
into serving.

Published v1 scheduler and Worker callbacks lack a shared transfer identity;
the binding keeps them as separate facts. On the local v2 host seam, the
scheduler passes its generation and recovery epoch to Workers, receives exact
Worker-incarnation receipts, and sends the accepted roster back for
first-compute observation. The plugin validates those links offline, without
joining on filenames, clocks across processes, or numeric job IDs alone.
Descriptor callbacks remain detached until the copy site has request identity
and ordering.

## Development

```bash
python -m pip install -e ".[test]" build
ruff check .
ruff format --check .
pytest -q
python -m build
```

CI covers Python 3.10, 3.12, and 3.14, source and clean-wheel tests, manifest
discovery, and ECPA plan/render behavior. Current host acceptance evidence and
remaining limits are recorded in
[docs/current_host_compatibility.md](docs/current_host_compatibility.md).

The host-independent schemas, sink bounds, normalization rules, and historical
source audit remain documented under [`docs/`](docs/).

Captured v2 JSONL records and descriptor inventories can be checked offline
with the [artifact validator](docs/artifact_validation.md). Its complete
restore-chain option accepts correlated recovery and first-compute records
from the local v2 host follow-up. Published v1.0 host output remains unjoined.
