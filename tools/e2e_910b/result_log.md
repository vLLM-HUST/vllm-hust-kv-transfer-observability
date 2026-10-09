# 910B E2E 验收记录（issue #2 毕业条件证据）

> 每条验收必须填：**命令 + 环境 + 原始结果 + 提交号**。原始结果=脚本输出原样粘贴，
> 不允许转述。此模板每个 Tier 一份，文件名为 `result_<tier>_<date>.md`。

## Tier N: <名称>（01_env_probe / 02_package_check / 02_serving / 03_perf）

### 环境

| 项 | 值 |
|---|---|
| 节点 | （.87 / .91 / 其他） |
| NPU | `npu-smi info` 摘要（型号/数量/驱动） |
| OS / Python | |
| vllm 版本 + commit | `python -c "import vllm; print(vllm.__version__)"` + `git rev-parse HEAD` |
| torch / torch_npu | |
| kv-transfer-observability 版本 | |
| extension-manager 版本 | |
| 模型 + 配置 | （如适用） |

### 执行命令（原样复制）

```bash
# 例如
bash 01_env_probe.sh | tee result_0_<date>.txt
```

### 原始结果

```
（粘贴完整输出，勿截断勿转述）
```

### 校验结论

- [ ] 通过 / [ ] 失败
- 失败原因分析（一句话根因）：
- 关联 issue/PR：kv-transfer-observability #2 / PR #4 / fork PR #

### 复跑记录（失败必填）

| 次数 | 日期 | 结果 | 备注 |
|---|---|---|---|
| 1 | | | |

---
## Tier 2/3 附：场景参数（serving 接线验证时填）

- 启动命令（含 `--kv-transfer-config` 全文）
- preemption 制造方式（并发数/序列长度/模型）
- 预期 6-event 链 vs 实际（verify_events.py --expect-restore-chain 输出）
- descriptor capture 清单
- 性能对比（Tier 3）：禁用 vs 启用，TTFT/TPOT/吞吐，±% 
