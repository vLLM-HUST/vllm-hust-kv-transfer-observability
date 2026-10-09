# 910B E2E test suite (tools/e2e_910b)

对 vllm-hust-kv-transfer-observability 的真实硬件验收套件，对应 issue #2
清单「在真实目标硬件完成端到端正确性和性能测试，并附命令、环境、原始结果和提交号」。

## 分层结构（Ascend 传输路径未确认前可降级）

| Tier | 脚本 | 内容 | 依赖 | 目的 |
|---|---|---|---|---|
| 0 | 01_env_probe.sh | 环境探测：npu-smi、fork commit、vllm 版本、Ascend offload 路径定位 | 集群 shell | 回答 host-adapter-proposal §3b（910B 传输路径阻塞项） |
| 1 | 02_package_check.sh | 包级验证：安装 + pytest + CLI inspect/check/enable 门禁 | 910B 任意环境 | 证明包在目标硬件可装可测、import_only 门禁生效 |
| 2 | （adapter 落地后） | serving 接线验证：起 OffloadingConnector → 造 preemption → 校验事件链 | adapter + 可跑 offload 的 910B | 6-event 链 / descriptor 落盘端到端 |
| 3 | （adapter 落地后） | 开销对比：禁用 vs 启用（TTFT/TPOT/吞吐） | 同 Tier 2 | HOST_CONTRACT 零开销语义实证 |

## 用法

```bash
# 在 910B 集群节点（Linux）：
bash 01_env_probe.sh | tee probe_result.txt     # Tier 0，先跑这个
bash 02_package_check.sh | tee pkg_result.txt   # Tier 1

# Tier 2/3 待 adapter 合并后补充 run_tier2.sh / run_tier3.sh
```

每个 Tier 的输出按 `result_log.md` 模板记录（命令/环境/原始结果/提交号），
作为 issue #2 毕业条件的证据附件。

## 校验器

`verify_events.py` 独立可用：校验 events.jsonl 的事件词表、schema、链顺序、
descriptor 文件的 4 键 schema 与无地址红线。

```bash
python3 verify_events.py --events /path/events.jsonl \
  --capture-dir /path/descriptors --expect-restore-chain request-xxx
```
