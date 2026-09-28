# Current-host compatibility preflight

Status: source/dependency preflight only (2026-09-28). No runtime, NPU,
activation, transfer correctness or performance support is claimed.

## Selected candidate and rejected combination

| Component | Candidate revision | Reason |
|---|---|---|
| upstream `vllm-project/vllm` | `ced6857afa0ea7b2e3f0846a62e1394e90f15607` | commit recorded by the selected Ascend tree in `.github/vllm-main-verified.commit` |
| `vLLM-HUST/vllm-ascend-hust` | `17f68177472b896facf3f0501d424d24e16e699a` | current audited Ascend source |

This candidate is upstream core, **not** a tested `vllm-hust` release or a claim
that the organization fork includes this exact tree. The marker's name is not
a substitute for our own runtime validation. Do not substitute current core
`main` silently when reproducing this pairing.

The previously audited organization core
`c7b61770097b5c6e9ae11d96baef59e3689231f1` paired with this Ascend tree fails a
source-level import check: Ascend's `model_runner_v1.py` imports
`RoutedExpertsLists` and `RoutedExpertsTensors` from `vllm.v1.outputs`, but that
core no longer defines them. Ascend `worker.py` imports the V1 runner at module
scope even when V2 is selected. Track the host mismatch through
[Ascend #34](https://github.com/vLLM-HUST/vllm-ascend-hust/issues/34), not through
local fake aliases or by suppressing ImportError.

## Checks performed

- Retrieved the exact upstream candidate and checked its real `outputs.py`:
  both routed-expert types and the corresponding ModelRunnerOutput field exist.
- Screened direct `from vllm... import ...` names against candidate source for
  Ascend V1 runner (77), V2 runner (16), native offloading connector (11), NPU
  specs (5), and CPU/NPU transfer implementation (12): none of the 121 screened
  names were unresolved. This AST/source screen checks names, not signatures,
  import side effects, binary ABI or transitive dependencies. Package-level or
  unavailable source modules were not counted; it is not a complete import test.
- Candidate V2 runner exposes `execute_model` and supplies request IDs to its
  connector pre-forward path; the same no-forward exclusion remains necessary.
- Ascend declares `torch==2.13.0`, `torch-npu==2.13.0rc1`, and
  `torchaudio==2.11.0`. A Python 3.12/aarch64 dependency dry-run for these three
  packages against PyPI resolved 31 packages. No packages were installed. This
  does not validate all Ascend requirements, native extensions or CANN.
- The dedicated plugin development environment has none of torch, torch-npu,
  vLLM or Ascend installed. No real host import or device test was run there.
- In a separate Python 3.12/aarch64 environment, the CPU-index builds of
  `torch==2.13.0`, `torchvision==0.28.0`, and `torchaudio==2.11.0`, plus
  `torch-npu==2.13.0rc1`, installed and imported successfully. Package metadata
  checks passed and `torch_npu.npu.is_initialized()` remained false. This
  tests only those packages, not the vLLM worker, native Ascend ops or hardware.
- Full dependency resolution of core `requirements/common.txt` and Ascend
  `requirements.txt` did not succeed: `triton-ascend==3.6.0` was unavailable
  from the checked default index, Huawei Ascend index and documented
  `triton-ascend/` find-links page. No alternate version was substituted.
- The selected Ascend Dockerfiles default to CANN 9.1.0; the local runtime
  installation is CANN 9.0.0. Successful TorchNPU import does not establish
  that this CANN combination supports the selected full Ascend tree. The
  system toolkit was not changed.
- The locally built plugin wheel was installed outside the checkout with
  Manager `701aa95ae8d5b23b5ea8c8ec475ceaa470393e54` in a clean Python 3.12
  environment: 143 plugin tests passed. `inspect` discovers the bundle,
  `check` reports `degraded` with unverified host/protocols, `plan` returns
  `inspect_only`, and `enable` is rejected with `import_only` (exit 2).
  These are expected negative activation results, not Manager run or real
  worker attachment evidence.

Reproduce the limited checks from the appropriate clones:

```bash
# Ascend clone, do not rely on a moving main:
git show 17f68177472b896facf3f0501d424d24e16e699a:.github/vllm-main-verified.commit
# Core clone; fetch the marker from its actual upstream owner:
git fetch https://github.com/vllm-project/vllm.git ced6857afa0ea7b2e3f0846a62e1394e90f15607 --depth=1 --filter=blob:none
git show ced6857afa0ea7b2e3f0846a62e1394e90f15607:vllm/v1/outputs.py
# An isolated Python 3.12 environment; this command does not install packages:
uv pip install --dry-run --python .venv/bin/python --index-url https://pypi.org/simple \
  'torch==2.13.0' 'torch-npu==2.13.0rc1' 'torchaudio==2.11.0'
```

## Remaining real integration work

1. Prepare an isolated full runtime environment from a coherent source pair,
   verify all dependencies/CANN and import the real worker/runner/connector.
   First resolve the unavailable pinned Triton Ascend package and the CANN
   version difference above, using documented artifacts rather than silently
   relaxing pins. The current isolated environment is dependency preflight,
   not an installed or supported runtime.
   If delivering on organization core, resolve its divergence from the Ascend
   marker before calling the combination supported.
2. Implement the minimal KV outlet tracked by
   [core #43](https://github.com/vLLM-HUST/vllm-hust/issues/43), reusing the shared
   bus work in core #3/#6. Neither audited current main nor this upstream
   candidate supplies the proposed complete KV observer API.
3. Implement the plugin's source-specific translation against that actual API
   and an explicitly enabled general-plugin startup entry point. Do not add an
   entry point that merely accepts fixture types or silently claims attachment
   when the host has no outlet.
4. Validate real worker coverage, exact transfer/recovery/first-compute identity,
   Manager launch and clean shutdown on hardware. Keep fixture results and
   source preflight separate from those results.

The shipped manifest remains `import_only`; its existing broad host version
field is not a compatibility declaration. No activation bypass or extra
approval mechanism is introduced by this preflight.
