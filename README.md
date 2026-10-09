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
- the host emits `runtime_effective` evidence only after the plugin callback
  returns `True` for a real transfer record;
- stop unregisters the observer and closes plugin-owned writer threads.

The carrier does **not** claim cross-process recovery-roster correlation,
descriptor capture, Ascend device qualification, transfer correctness, or a
performance benefit. Those remain separate gates. In particular, successful
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

Scheduler-side recovery admission and first-compute records currently lack a
plugin-visible cross-process transfer identity. The binding therefore rejects
them instead of correlating by time, filename, or job-number coincidence.
Descriptor callbacks likewise remain detached until ordering and request
identity are available at the copy site.

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

## Canonical MOD metadata

Repository identity, directly responsible maintainers, advisor status, default-off
activation, rollback, scope, and evidence qualification are recorded in
[`MOD_METADATA.json`](MOD_METADATA.json). `advisor_status: unknown` is not the
same as confirmed `none`. Performance statements remain limited to the workloads
and evidence labels recorded there; they are not general online claims.
