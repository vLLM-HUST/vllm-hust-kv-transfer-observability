# Current host compatibility (2026-10-12)

The merged [core #46](https://github.com/vLLM-HUST/vllm-hust/pull/46)
publishes the default-off v1 transfer observer. Merged
[core #48](https://github.com/vLLM-HUST/vllm-hust/pull/48) adds the optional
v2 scheduler/Worker generation and recovery-roster protocol. Merged
[plugin #12](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/pull/12)
consumes those protocols. The v1-only path still writes separately scoped
host facts; it cannot attest a cross-process restore chain.

## Exact checked baseline

### Clean package pairing (supersedes the diagnostic packaging gaps below)

Core main `5a820400f` was built as a device-free wheel for the external NPU
backend (`VLLM_TARGET_DEVICE=empty`). Ascend `575bd1c265`, with the local
compatibility/call-site changes described below and Transformers pinned to
5.19.0, completed its full wheel build, including the native binding and
CANN custom operators. Both wheels, the plugin wheel and Extension Manager
`5aa797cdc` were installed into a newly created Python 3.12 environment.
No editable installation or source `PYTHONPATH` override was used.

This candidate uses Torch 2.13.0+cpu, TorchNPU 2.13.0rc1, Transformers
5.19.0, NumPy 2.2.6 and Triton-Ascend 3.6.0+dev20260921 on one 910B2 with
CANN 9.1.0. The Triton-Ascend wheel comes from the official
[nightly index](https://mirrors.huaweicloud.com/ascend/repos/pypi/nightly/triton-ascend/),
but its NumPy requirement was locally changed from 1.26.4 to `>=2,<2.3`.
This is a locally adapted candidate, not an unmodified official package or
a general compatibility claim. Upstream Triton 3.6.0 must be installed
before Triton-Ascend because they share Python module paths.

Verified results in the clean environment:

- All 190 installed packages pass `uv pip check`, including Ascend.
- Extension Manager inspect/check/enable/plan/render/disable pass;
  installation remains default-off and check reports compatible/configured.
- The plugin's 250 tests pass.
- The official Triton-Ascend vector-add example runs on NPU with a fresh
  compilation cache and maximum difference 0 from TorchNPU.
- Qwen3-0.6B real CPU KV offload/restore returns `[4236, 11]` for both cold
  and restored inference. All five observer records validate. This simple
  probe does not exercise a scheduler-preemption recovery chain.
- Spawned EngineCore exits successfully with an explicit 30-second
  shutdown grace period; no forced kill was needed.
- An 80 MiB, three-request KV-pressure run produces 30 valid records,
  including two complete recovery episodes and 12 correlated host records.
  All three output digests match the earlier diagnostic.
- Live Worker stop returns true; a second real restore succeeds and leaves
  the record count unchanged at five. Only this test enables callable
  serialization for its Worker RPC; that is not a production setting.

Local raw evidence is under `verification-current-20261009/`:
`ascend-wheel-build.log`, `clean-ascend-install.log`,
`clean-installed-provenance.log`, `clean-requirements.txt`,
`clean-manager-check.log`, `clean-plugin-tests.log`,
`clean-nightly-vector-add.log`, `clean-restore*.log`/JSONL,
`clean-pressure*.log`/JSONL and `clean-live-stop.log`/JSONL.
On 2026-10-10, Triton source `5aef383d` completed its full wheel build with
the same NumPy constraint. The successful build uses the bundled LLVM 22
linker before CANN's LLD 15 on PATH. Its
`triton_ascend-3.6.0+git5aef383d-cp312-cp312-linux_aarch64.whl` contains the
native compiler library and passed ZIP integrity checks. It replaced the
nightly in the clean environment through the normal dependency resolver;
all 190 packages remain compatible. Fresh-cache NPU vector add again has
maximum difference 0, and 273 combined plugin/Ascend tests pass. These are
local source changes, not an upstream release. Exact installed origins,
requirements and results are in `source-installed-provenance.log`,
`source-requirements.txt`, `source-wheel-inspection.log`,
`source-pip-check.log`, `source-vector-add.log`, and `source-package-tests.log`.
The source-built combination also passes the Manager lifecycle check and
real 80 MiB/three-request pressure recovery: 30 records, two complete
recovery episodes, identical output digests to the nightly run, and clean
shutdown (`source-manager-check.log`, `source-pressure.log`,
`source-pressure-events.jsonl`, `source-pressure-validation.log`).
Live stop and disabled restore also pass on this final source-built stack:
stop returns true and the second restore leaves five records unchanged;
disabled cold/restored IDs match `[4236, 11]` and no event file is created.
Both exit normally (`source-live-stop.log`, `source-disabled.log`).

## Descriptor identity and restart checks

The host observer API is v1.1. Each accepted Worker submission now creates a
short-lived token. The copy site records its bounded, region-relative layout
inside that submission context; after the backend accepts the submission the
host publishes the layout with request ID, rank, Worker generation and token.
The plugin creates one transfer identity and requires the same identity for
the descriptor inventory and terminal transfer event. Rejected submissions,
wrong tokens, duplicate layouts and incomplete inventories are discarded.
The token is internal and is not written to the user event schema.

With the rebuilt core/plugin wheels and source Triton wheel, the real 910B2
pressure run produced five descriptor inventories. Offline validation accepted
30 events, 12 correlated host records, two complete recovery episodes and all
five inventories (`descriptor-validation-20261011.log`). The descriptor files
contain only relative offsets, sizes, region IDs and bounded identity. A
separate clean process produced a different Worker generation and transfer
namespace with two validated inventories (`descriptor-restart-validation.log`).

The backend-failure unit test reports `success=false` before preserving the
host's existing fail-stop behavior; no successful receipt is emitted. Existing
adapter and sink tests cover event-write failure without raising into serving.
Wheel uninstall removes the plugin import, reinstall restores a zero-conflict
environment, and Extension Manager enable/disable/forget in temporary state
leaves the bundle disabled with no activation intent.

On 2026-10-12, separate real 910B2 pressure runs each validated 30 events,
two complete recoveries and five descriptor inventories. Both reused restore
job IDs 2 and 4 but had disjoint Worker generations and transfer namespaces.
Removing one correlated receipt from an artifact copy made chain validation
reject it. A controlled failed backend **report** on the real NPU exposed a
field mismatch: the local core included a non-null transfer size on failure,
which the plugin refused as success evidence. The local core now publishes
`bytes_moved=None` on failure; its rebuilt wheel produced a `transfer_failed`
record without a successful receipt before the existing host fail-stop. The
underlying transfer was not physically failed. A separate plugin-writer ENOSPC
injection left all three model outputs correct while counting 30 I/O errors.
Live stop again held the event count at five after a second successful
restore. An ABBA off/on/on/off timing sample showed mean generation times
22.785/22.679 s with matching outputs; the observed −0.46% difference is
within sample variation and does not establish a performance claim. Actual
wheel uninstall, real event-free serving, reinstall, Manager lifecycle and
fresh enabled recovery also passed. Full local evidence is
`verification-current-20261009/single-card-20261012/RESULTS.md`.

The current local qualification remains one 910B2 with the V1 model runner.
It does not cover multi-rank, V2 model runner, physical device faults,
in-place Worker restart, HTTP serving, or long-running multi-process rollback.

### Earlier source-based diagnostic

| Component | Revision or environment | Result |
| --- | --- | --- |
| Core source | `vLLM-HUST/vllm-hust` main `5a820400f` | 73 focused observer, recovery, capability and plugin tests passed. |
| Plugin source | this repository main `05fcf5c` | 250 tests, Ruff, wheel build and isolated wheel installation passed. |
| Extension Manager | `5aa797cdc` | Isolated wheel `inspect`, `check`, `enable`, `plan`, `render`, and `disable` passed with merged core source on `PYTHONPATH`; `check` reported `compatible` and `configured`. The virtual environment used a temporary Triton 3.6 overlay and older installed core version metadata, so this is a source/capability check, not a clean merged-core installation. |
| Ascend source | `575bd1c265` plus uncommitted local compatibility and call-site changes | 15 focused native CPU-offload tests passed. This is a diagnostic candidate, not a package-supported revision. |
| 910B2 device | Python 3.12, Torch 2.13.0+cpu, TorchNPU 2.13.0rc1, CANN 9.1.0, Transformers 5.19.0, source Triton-Ascend 3.6.0+git5aef383d | Real V1 CPU KV offload, recovery and descriptor capture passed in the clean wheel environment. |

The Ascend candidate incorporates collaborator commit `3a5912bb92a8` from
`feat/kv-transfer-observability-call-sites`: region-relative copy descriptors
in `cpu_npu.py` and the V1 real-forward callback. It also guards imports of
model-specific `fla_npu` and a removed core V2 speculative-decode module,
and adapts Ascend's V1 runner to the merged core's removed `PassConfig.enable_sp`
and `resolve_cudagraph_mode_and_sizes(tensor_parallel_size=...)` arguments.
The latest checked Ascend main `8770e692` did not pass import with core
`5a820400f`: after optional-import guards, it requested the absent
`maybe_prepare_dcp_local_seq_lens` symbol. The older source candidate is
therefore the current core-first diagnostic path.

## Real-device observations on the merged core/plugin source

These runs used one 910B2, Qwen3-0.6B, the Ascend V1 runner, default `fork`,
and native CPU KV offload. Commands are the existing
`runtime-npu/run_cpu_kv_restore.py`, `run_preemption.py 80 3 no-prefix`, and
`run_disable_during_serving.py`, with merged core and plugin source plus the
local Ascend candidate on `PYTHONPATH`. Original stdout and JSONL files are
preserved in the local workspace's `verification-current-20261009/` directory.

| Probe | Observed result |
| --- | --- |
| Enabled KV restore | Cold and restored output IDs were both `[4236, 11]`. The JSONL file has four canonical v2 D2H/H2D start/completion records and one separately scoped v1 host receipt. The installed package validator accepted all five records for schema validation. |
| KV-pressure recovery | Three requests each generated 145 tokens. The output digests matched the earlier local diagnostic run. The 30-record JSONL file has 12 correlated v2 host records; `validation --expect-restore-chain 1-aa6f8cde` accepted two complete recovery episodes. Eight v1 host facts remain separately scoped. |
| Disabled plugin | The same simple KV restore produced matching output IDs and no event file. |
| Live stop | Worker-side `stop_plugin()` returned true after the first restore wrote five records. A second restore returned the same token IDs and left the record count at five. This isolated test used `VLLM_ALLOW_INSECURE_SERIALIZATION=1` solely to carry its test callable through Worker RPC; it is not a production setting. |

The earlier diagnostic used a separately built `vllm_ascend_C` binding copied into
the source tree. At that point no full Ascend wheel/custom-operator build or clean combined
installation had passed. Unmodified Ascend declares `transformers==5.14.1` while
core requires `>=5.16.1,<5.20.0`; Ascend also declares
`triton-ascend==3.6.0`, which was absent from the checked package index. The
available 3.2.2 environment produces ten `uv pip check` incompatibilities.
The checked [Triton-Ascend release list](https://github.com/triton-lang/triton-ascend/releases)
ends at 3.2.2; the separate nightly index was found later, as described above.
An isolated dependency experiment installed 3.2.2's declared NumPy 1.26.4,
Triton 3.5.0, and other pins; that reduced the environment report to four
conflicts but introduced a hard conflict with core's
`opencv-python-headless>=4.13` (OpenCV 4.13 requires NumPy >=2 on Python
3.12). No clean core-first package pairing was produced. The experimental
3.2.2 pin change was reverted from the Ascend candidate.
Changing requirement text alone would not prove the binaries and runtime
compatible.

The earlier source diagnostic had no descriptor association. The rebuilt
v1.1 host/plugin path now associates the copy-site record with the accepted
submission as described above. Multi-rank, V2 runner, backend-fault
degradation beyond the tested fail-stop/report behavior, repeated
rollback/uninstall, and steady-state performance remain unqualified.
The six-sample timing probe in the earlier local experiment was exploratory
only; it is not used as a performance claim.

The local V1/Qwen3 single-device package pair now has installable versions,
real recovery evidence and descriptor identity evidence. The next single-card
checks are in-place Worker restart, long-running service teardown, and V2
model-runner compatibility. Multi-rank testing is deferred for this stage.
Complete the remaining fault and matched-performance gates in
[Issue #2](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/issues/2).
