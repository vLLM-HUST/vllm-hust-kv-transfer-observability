#!/usr/bin/env bash
# Tier 0: 环境探测 —— 回答 host-adapter-proposal §3b:
# 910B 上 OffloadingConnector 是否可起 / GPU<->CPU 传输走哪条代码。
# 用法: bash 01_env_probe.sh | tee probe_result.txt
set -uo pipefail

echo "===== [1/6] NPU 硬件 ====="
npu-smi info 2>&1 | head -30 || echo "npu-smi 不可用"

echo ""
echo "===== [2/6] Python / vLLM / 包版本 ====="
python3 --version 2>&1
python3 -c "import vllm; print('vllm', vllm.__version__, vllm.__file__)" 2>&1
python3 -c "import torch; print('torch', torch.__version__)" 2>&1
python3 -c "import torch_npu; print('torch_npu', torch_npu.__version__)" 2>&1 || echo "torch_npu 不可用"

echo ""
echo "===== [3/6] vllm-hust fork commit ====="
VLLM_PATH=$(python3 -c "import vllm, os; print(os.path.dirname(os.path.dirname(vllm.__file__)))" 2>/dev/null)
if [ -d "$VLLM_PATH/.git" ]; then
  git -C "$VLLM_PATH" rev-parse HEAD 2>&1
  git -C "$VLLM_PATH" log --oneline -3 2>&1
  git -C "$VLLM_PATH" remote -v 2>&1 | head -2
else
  echo "vllm 安装路径无 .git（$VLLM_PATH），可能为 pip 安装:"
  echo "$VLLM_PATH"
fi

echo ""
echo "===== [4/6] kv-transfer-observability 包 ====="
python3 -c "import vllm_hust_kv_transfer_observability as m; print('pkg', m.__file__)" 2>&1 || echo "包未安装（Tier 1 会装）"

echo ""
echo "===== [5/6] OffloadingConnector / Ascend 传输路径定位 ====="
echo "--- kv-transfer-config 参数存在性 ---"
python3 -m vllm.entrypoints.openai.api_server --help 2>&1 | grep -i "kv-transfer-config" || echo "cli 无 kv-transfer-config（或 --help 解析失败）"
echo "--- 传输实现文件扫描（npu/swap/triton/dma） ---"
SEARCH_DIR=${VLLM_PATH:-$(python3 -c "import vllm, os; print(os.path.dirname(vllm.__file__))" 2>/dev/null)}
if [ -n "${SEARCH_DIR:-}" ]; then
  grep -rl "swap_blocks_batch\|torch_npu\|_C_ascend" "$SEARCH_DIR/vllm/v1/kv_offload" 2>/dev/null || echo "kv_offload 内无 NPU 专用实现"
  ls "$SEARCH_DIR/vllm/v1/kv_offload/cpu/" 2>/dev/null
fi

echo ""
echo "===== [6/6] 网络/环境杂项 ====="
env | grep -i "^VLLM_\|^ASCEND\|^NPU" || echo "无相关 env"
free -g 2>&1 | head -3
df -h /tmp /mnt 2>&1 | head -5

echo ""
echo "===== probe done: 把本文件 + npu-smi 完整输出存入 result_log.md ====="
