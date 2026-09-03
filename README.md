# KV Transfer and Recovery Observability

Owner-led migration carrier for identity-bound KV transfer, recovery,
descriptor-layout, and first-compute receipts. It observes runtime behavior and
does not own KV storage or service lifecycle.

**Status: address-free event and descriptor sinks are installable and tested;
automatic vLLM attachment remains blocked until the host contracts in
`HOST_CONTRACT.md` exist.** The package stays `import_only`: installing it never
changes vLLM behavior, and Extension Manager refuses enablement.

Technical ownership belongs to @xiehanlong834-gif, @Remygred. Source extraction
must preserve exact authorship, license, tests, constraints, and evidence before
activation is considered.

See [MAINTAINERS.md](MAINTAINERS.md), [PROVENANCE.md](PROVENANCE.md), and
[docs/semantic-audit.md](docs/semantic-audit.md) (B134 semantic audit).

## Extension framework

Extension ID: `org.vllm-hust.kv-transfer-observability`

This repository follows the vLLM-HUST Extension Template. The current package
is deliberately `import_only`: the observability primitives can be imported and
tested, but Extension Manager must refuse enablement until explicit observer
registration, configuration, and compatibility evidence land.

## Install

```bash
python -m pip install "vllm-hust-ext @ git+https://github.com/vLLM-HUST/extension-manager.git@main"
python -m pip install -e ".[test]"
```

Installation alone changes no vLLM behavior. The static Manifest 0.2 descriptor
lives inside the Python distribution under `src/`.

## Inspect and test

```bash
vllm-hust-ext extension inspect org.vllm-hust.kv-transfer-observability
vllm-hust-ext extension check org.vllm-hust.kv-transfer-observability
pytest -q
```

`extension check` reports the discovered state; `extension enable` is expected
to fail with `implementation status: import_only` until the graduation gate in
issue #2 (clean-wheel install, compatibility rejection, real activation, fault
degradation, rollback, uninstall) passes.

## Developer usage of the sinks

Both sinks are explicit-opt-in and write nothing by default:

```python
from vllm_hust_kv_transfer_observability import (
    DescriptorLayoutCapture,
    JsonlKVTransferEventSink,
)

# Event sink: pass a path to enable, None keeps it inert. request_id always
# denotes the request; jobs/epochs/ranks use their own optional fields.
with JsonlKVTransferEventSink("/tmp/kv-transfer-events.jsonl") as sink:
    sink.emit("restore_done", "request-1", keys=16)
    sink.emit(
        "transfer_submit",
        "request-1",
        transfer_id="job-7",
        recovery_epoch=2,
        rank=3,
        bytes=4096,
    )

# Descriptor capture: directory must already exist; files are 0600, O_EXCL.
capture = DescriptorLayoutCapture("/tmp/kv-layouts", "real-online")
capture.capture(
    job_id=7,
    direction="h2d",
    descriptors=[{"src_offset": 0, "dst_offset": 8, "size": 16, "direction": "h2d"}],
)
```

Both sinks are fail-closed and address-free by implementation, not by promise:

- `JsonlKVTransferEventSink.emit` rejects event names outside the adapter
  vocabulary (`EVENT_VOCABULARY_V1`, 10 events, see `HOST_CONTRACT.md`) and
  rejects address-like field names (e.g. `src_address`, `data_ptr`); the
  disabled path stays a single no-op guard.
- `DescriptorLayoutCapture` rejects any descriptor that is not exactly the
  four-key v1 schema, and captured files never contain address material.

## Uninstall

```bash
python -m pip uninstall vllm-hust-kv-transfer-observability
```

No daemon, file watcher, or vLLM hook is registered by the package, so
uninstalling (or `pip uninstall` after a clean-wheel install) fully removes it
without rollback steps.
