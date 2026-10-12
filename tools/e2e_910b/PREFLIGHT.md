# 910B preflight (2026-10-12)

The old 2026-09-29 preflight predates the merged host and plugin changes.
[core #46](https://github.com/vLLM-HUST/vllm-hust/pull/46),
[core #48](https://github.com/vLLM-HUST/vllm-hust/pull/48), and
[plugin #12](https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability/pull/12)
are merged. The plugin Manifest 0.3 carrier is activatable after an explicit
enable; installation alone does not activate it. Tier 1 must check the real
host capability registry, rather than expecting an `import_only` refusal.

One 910B2 diagnostic has already run real CPU KV offload/restore and two
complete correlated recovery episodes. It used core code before the final
#48 merge, an isolated Ascend source tree at `575bd1c265` with local fixes,
and a directly built native binding. It does not establish a compatible
package installation, V2 runner, multi-rank behavior, descriptor correlation,
graceful backend-fault recovery, or stable overhead. Exact commands and raw
artifacts are under the local workspace's `runtime-npu/`; see
`docs/current_host_compatibility.md`.

A 2026-10-09 rerun using the merged core/plugin source and the same Ascend
candidate again produced real KV restore, two complete recovery episodes,
disabled-plugin silence, and live-stop behavior. A subsequent clean-wheel
installation also passed dependency checking, Manager lifecycle checks,
real restore, two pressure-recovery episodes, disabled-plugin silence and
live stop. See the exact locally adapted package combination and remaining
limits in `docs/current_host_compatibility.md`; broader device gates remain open.

Ascend collaborator commit `3a5912bb92a8` supplies the two planned copy-site
descriptor and V1 first-compute call sites. They remain outside Ascend `main`.
The latest checked Ascend main `8770e692` fails import against merged core
`5a820400` in the current environment; Ascend `575bd1c265` with the
call sites and local import guards passes the focused native CPU-offload tests.
The unmodified candidate's Transformers 5.14.1 pin conflicts with core's
`>=5.16.1,<5.20.0`. The local full Ascend wheel uses 5.19.0 instead.
Triton-Ascend 3.6 is available from the official Huawei nightly index;
the tested local nightly package relaxes its NumPy pin to `>=2,<2.3`,
allowing core's OpenCV dependency. The clean 190-package set passes
`uv pip check` and NPU correctness probes. This is a local adaptation,
not an unmodified upstream-supported combination.
The subsequent source build at Triton `5aef383d` now also passes the same
dependency check, fresh-cache NPU vector add, 273 focused tests, Manager
lifecycle and two complete real pressure-recovery episodes. The tested
wheel is `3.6.0+git5aef383d`, built with its bundled LLVM 22 linker.

Tier 0 (`01_env_probe.sh`) records the machine. Tier 1
(`02_package_check.sh`) now takes an exact wheel and requires a Python
environment with the matched host installed; it fails if the capability
check degrades or dependencies conflict. Run it in a disposable environment:

```bash
KV_OBSERVER_PYTHON=/path/to/venv/bin/python \
KV_OBSERVER_UV=/path/to/uv \
bash 02_package_check.sh /path/to/plugin.whl
```

The older `verify_events.py` script is a historical Tier 0/1 artifact and
must not be used to certify the v2 recovery chain. No committed Tier 2/3
driver exists yet. The locally rebuilt host/plugin wheels have passed an
identity-matched descriptor run, a controlled failed backend-report test,
plugin-writer ENOSPC injection, live stop, and uninstall/reinstall on one
910B2. See `docs/current_host_compatibility.md` and the local raw artifacts
under `verification-current-20261009/single-card-20261012/`. V2 model-runner,
in-place Worker restart, HTTP serving, physical device faults, and broad
overhead characterization remain untested; multi-card is outside this stage.
