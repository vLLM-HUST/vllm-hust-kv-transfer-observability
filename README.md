# KV Transfer and Recovery Observability

Owner-led migration carrier for identity-bound KV transfer, recovery, descriptor-layout, and first-compute receipts. It observes runtime behavior and does not own KV storage or service lifecycle.

**Status: address-free event and descriptor sinks are installable and tested; automatic vLLM attachment remains blocked until the host contracts in `HOST_CONTRACT.md` exist.**

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
