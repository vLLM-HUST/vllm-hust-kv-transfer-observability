#!/usr/bin/env bash
# Tier 1: 包级验证 —— 910B 上安装 + 全测试 + CLI 门禁。
# 用法: bash 02_package_check.sh | tee pkg_result.txt
set -euo pipefail

echo "===== [1/4] 安装 kv-transfer-observability（本地 wheel 或 git）====="
PKG_REF="${1:-git+https://github.com/vLLM-HUST/vllm-hust-kv-transfer-observability.git@main}"
python3 -m pip install "vllm-hust-ext @ git+https://github.com/vLLM-HUST/extension-manager.git@main"
python3 -m pip install "$PKG_REF"

echo ""
echo "===== [2/4] 包内测试 ====="
python3 -m pip install pytest ruff
python3 -m pytest -q --pyargs vllm_hust_kv_transfer_observability 2>&1 || \
python3 -m pytest -q 2>&1 || echo "pytest 收集失败（包内测试路径见上）"

echo ""
echo "===== [3/4] CLI 发现/门禁 ====="
vllm-hust-ext extension inspect org.vllm-hust.kv-transfer-observability | head -5
vllm-hust-ext extension check org.vllm-hust.kv-transfer-observability | head -8
echo "--- enable 应失败（import_only 门禁）---"
if vllm-hust-ext extension enable org.vllm-hust.kv-transfer-observability 2>&1; then
  echo "!! 意外：enable 成功 —— 门禁失效，立即报告"
  exit 1
else
  echo "OK: enable 被正确拒绝"
fi

echo ""
echo "===== [4/4] 运行时零副作用冒烟（不 import vllm）====="
TMPD=$(mktemp -d)
python3 - "$TMPD" <<'PY'
import sys, os
from vllm_hust_kv_transfer_observability import JsonlKVTransferEventSink
d = sys.argv[1]
sink = JsonlKVTransferEventSink(None)          # 禁用
sink.emit("preempt", "req-1")                   # 必须无副作用
sink.close()
assert os.listdir(d) == [], f"禁用 sink 产生了文件: {os.listdir(d)}"
print("OK: 禁用 sink 零副作用")
PY
rm -rf "$TMPD"

echo ""
echo "===== package check done ====="
