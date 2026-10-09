# Tier 2/3 前置条件与缺口（2026-09-29 记录）

本文件只记录"能不能跑"的事实与前置条件。**不含任何未实测的结论。**
Tier 0/1 的脚本可立即在目标环境执行；Tier 2/3 的 driver 脚本尚未编写，
因为其依赖项尚未满足（理由见下），编写一个跑不起来的 driver 没有意义。

## 现在就能跑（不依赖宿主接线）

| Tier | 脚本 | 依赖 |
|---|---|---|
| 0 | `01_env_probe.sh` | 集群 shell（npu-smi） |
| 1 | `02_package_check.sh` | 910B 上任意能装 `vllm-hust-ext` 的 Python 环境 |

两者都不需要 vLLM 服务能起来。

## Tier 2/3 的前置条件（当前均未满足）

1. **宿主侧出口未落地**。`vLLM-HUST/vllm-hust#46`（默认关闭的
   `register_kv_transfer_observer` seam、`emit_kv_transfer_descriptors`、
   `build_region_descriptors`）仍为 OPEN，未进 vllm-hust main。
   Ascend 侧的两个调用点（`vllm_ascend/distributed/kv_transfer/kv_pool/kv_offload/native/cpu_npu.py`、
   `vllm_ascend/worker/model_runner_v1.py`）已在
   `feat/kv-transfer-observability-call-sites`（commit `3a5912bb92a8`）写好，
   但按该提交自身的说明"parked until that PR lands; the PR and its unit tests
   come with it"——**在 #46 合入前不具备可运行性**。

2. **插件侧接线点仍未完成**。`KVTransferHostAdapter`
   （`start(HostObserverBinding)` / `observe(...)` / `capture_descriptor(...)` /
   `stop()`，见 `src/.../adapter.py`）已在 main，但"把插件接到真实宿主出口"
   仍是 **DRAFT PR #7 `feature/issue2-current-host-binding`** 的 next step
   （该 PR 正文自述：does not implement a real source binding）。

3. **需要一台能真正跑起 vLLM 服务的 910B 环境**，已知当前不满足：
   - `.86`（aarch64，:32022）：triton-ascend 与 triton 版本错配，服务能起、首请求崩；
   - `.87`（:32012）：vllm-hust 树与 vllm-ascend-hust 缺
     `_get_packed_kv_cache_groups` / `RoutedExpertsLists`；
   - 结论：Tier 2/3 需要**配套 commit 对**的镜像/环境，不是本机或现有测试机可完成。

## 写 driver 时必须使用的真实 API 名（勿臆造）

- 宿主侧（#46 分支）：
  `register_kv_transfer_observer`、`emit_kv_transfer_descriptors`、
  `build_region_descriptors`、`kv_transfer_observers_configured`、
  `TransferOperation`、`observe_forward_batch`
- 插件侧（main）：
  `KVTransferHostAdapter`、`ObserverConfig.from_mapping`、`HostObserverBinding`、
  `HostObserverCallbacks`、`HOST_OBSERVER_CONTRACT` = `"vllm.kv-transfer.observer.v1"`

## 校验器

`verify_events.py` 已随本目录提供，编译通过；可在任何 Python 3 环境独立运行，
用于校验 events.jsonl 的事件词表/schema/链顺序，以及 descriptor 文件的 4 键
schema 与"无地址"红线。

## 复现本目录的落地状态

本目录此前是未跟踪文件（不在任何 commit 中）。2026-09-29 已在一个分支上纳入版本控制，
以免这套 issue #2 的验收材料丢失。
