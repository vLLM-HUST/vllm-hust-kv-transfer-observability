#!/usr/bin/env bash
# Tier 1: verify a built wheel in an isolated Python environment with a real host.
set -euo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  printf 'Usage: KV_OBSERVER_PYTHON=/path/to/venv/bin/python %s /path/to/plugin.whl\n' "$0" >&2
  exit 2
fi

: "${KV_OBSERVER_PYTHON:?Set KV_OBSERVER_PYTHON to an isolated venv Python}"
PYTHON_BIN=$KV_OBSERVER_PYTHON
UV_BIN=${KV_OBSERVER_UV:-uv}
WHEEL=$(realpath "$1")
EXTENSION_ID=org.vllm-hust.kv-transfer-observability
MANAGER_REF=${KV_OBSERVER_MANAGER_REF:-'vllm-hust-ext @ git+https://github.com/vLLM-HUST/extension-manager.git@5aa797cdc80d2d628b8f0e1548c53ff484df534b'}

"$UV_BIN" pip install --python "$PYTHON_BIN" "$MANAGER_REF" "$WHEEL"
"$UV_BIN" pip check --python "$PYTHON_BIN"
CLI_DIR=$("$PYTHON_BIN" -c 'import os, sys; print(os.path.dirname(sys.executable))')
CLI="$CLI_DIR/vllm-hust-ext"

RESULT_TMP=$(mktemp -d)
trap 'rm -rf "$RESULT_TMP"' EXIT
export XDG_CONFIG_HOME="$RESULT_TMP/config"
export XDG_DATA_HOME="$RESULT_TMP/data"

"$CLI" extension inspect "$EXTENSION_ID" | tee "$RESULT_TMP/inspect.json"
"$PYTHON_BIN" - "$RESULT_TMP/inspect.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    record = json.load(stream)
if record["enabled"] is not False:
    raise SystemExit("wheel installation unexpectedly enabled the plugin")
if record["activation_ready"] is not True:
    raise SystemExit("installed wheel is not activatable")
PY

"$CLI" extension check "$EXTENSION_ID" | tee "$RESULT_TMP/check.json"
"$PYTHON_BIN" - "$RESULT_TMP/check.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    states = set(json.load(stream)["states"])
if not {"installed", "discovered", "compatible", "configured"} <= states:
    raise SystemExit(f"host capability check is incomplete: {sorted(states)}")
if "degraded" in states:
    raise SystemExit("host capability check is degraded")
PY

"$CLI" extension enable "$EXTENSION_ID"
"$CLI" extension plan "$EXTENSION_ID"
"$CLI" extension render "$EXTENSION_ID"
"$CLI" extension disable "$EXTENSION_ID"
