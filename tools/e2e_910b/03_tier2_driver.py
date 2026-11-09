#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tier 2 acceptance driver for one qualified core/Ascend pair.

The driver starts a real OpenAI server with the CPU offloading connector and
the plugin bundle explicitly enabled, drives store/reload/preemption traffic,
validates the raw artifacts with the installed package's offline validator,
records graceful-teardown behaviour, and writes a manifest holding the exact
commands, environment, revisions, and SHA256 digests.

It never reports a pass for evidence it did not collect: a missing transfer
event, descriptor inventory, or complete restore chain fails the run. It
produces no latency or throughput number and makes no performance claim.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EXTENSION_ID = "org.vllm-hust.kv-transfer-observability"
ENABLED_BUNDLES_ENV = "VLLMHUST_EXT_ENABLED_BUNDLES"
EVENT_PATH_ENV = "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVENT_PATH"
DESCRIPTOR_DIR_ENV = "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_DESCRIPTOR_DIR"
EVIDENCE_LABEL_ENV = "VLLM_HUST_KV_TRANSFER_OBSERVABILITY_EVIDENCE_LABEL"
VALIDATION_MODULE = "vllm_hust_kv_transfer_observability.validation"
EVIDENCE_LABELS = (
    "existing-server-probe",
    "real-online",
    "replay",
    "simulation/model",
)
MAX_SHA256_BYTES = 256 * 1024 * 1024

PROBE_SCRIPT = r"""
import json
import platform
import sys

record = {"python": platform.python_version(), "executable": sys.executable}
try:
    from importlib.metadata import version

    record["plugin_version"] = version("vllm-hust-kv-transfer-observability")
except Exception as exc:  # noqa: BLE001 - reported, not raised
    record["plugin_version"] = None
    record["plugin_error"] = type(exc).__name__
try:
    from vllm_hust_kv_transfer_observability import native

    record["plugin_required_host_api"] = native.REQUIRED_HOST_API_VERSION
    record["plugin_correlated_host_api"] = native.CORRELATED_HOST_API_VERSION
except Exception as exc:  # noqa: BLE001 - reported, not raised
    record["plugin_required_host_api"] = None
    record["plugin_error_contract"] = type(exc).__name__
try:
    from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
        observability as host,
    )

    record["host_observability_api"] = getattr(
        host, "KV_TRANSFER_OBSERVABILITY_API_VERSION", None
    )
except Exception as exc:  # noqa: BLE001 - reported, not raised
    record["host_observability_api"] = None
    record["host_observability_error"] = type(exc).__name__
try:
    from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
        correlated_observability as correlated,
    )

    record["host_correlated_api"] = getattr(
        correlated, "KV_TRANSFER_CORRELATED_OBSERVABILITY_API_VERSION", None
    )
except Exception as exc:  # noqa: BLE001 - reported, not raised
    record["host_correlated_api"] = None
    record["host_correlated_error"] = type(exc).__name__
for module in ("vllm", "torch", "torch_npu"):
    try:
        imported = __import__(module)
        record[module] = getattr(imported, "__version__", None)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        record[module] = None
        record[module + "_error"] = type(exc).__name__
print(json.dumps(record, sort_keys=True))
"""


class DriverError(RuntimeError):
    """A step could not be completed; the run is not evidence of a pass."""


def _encode(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def kv_transfer_config(
    cpu_bytes_to_use: int,
    *,
    spec_name: str = "CPUOffloadingSpec",
    eviction_policy: str = "lru",
    canonical_layout: bool = True,
    block_size: int | None = None,
) -> dict:
    """Build the connector config the descriptor capture path requires."""

    if type(cpu_bytes_to_use) is not int or cpu_bytes_to_use <= 0:
        raise ValueError("cpu_bytes_to_use must be a positive integer")
    if type(canonical_layout) is not bool:
        raise ValueError("canonical_layout must be a bool")
    extra: dict[str, object] = {
        "spec_name": spec_name,
        "cpu_bytes_to_use": cpu_bytes_to_use,
        "eviction_policy": eviction_policy,
        "canonical_layout": canonical_layout,
    }
    if block_size is not None:
        if type(block_size) is not int or block_size <= 0:
            raise ValueError("block_size must be a positive integer")
        extra["block_size"] = block_size
    return {
        "kv_connector": "OffloadingConnector",
        "kv_role": "kv_both",
        "kv_connector_extra_config": extra,
    }


def plugin_environment(
    out_dir: Path,
    evidence_label: str,
    *,
    descriptor_capture: bool = True,
) -> dict:
    """Environment for the serving process; activation stays explicit."""

    if evidence_label not in EVIDENCE_LABELS:
        raise ValueError(f"unsupported evidence label: {evidence_label}")
    environment = {
        ENABLED_BUNDLES_ENV: EXTENSION_ID,
        EVENT_PATH_ENV: str(out_dir / "events.jsonl"),
        "VLLM_SERVER_DEV_MODE": "1",
    }
    if descriptor_capture:
        environment[DESCRIPTOR_DIR_ENV] = str(out_dir / "descriptors")
        environment[EVIDENCE_LABEL_ENV] = evidence_label
    return environment


def server_argv(
    python: str,
    *,
    model: str,
    host: str,
    port: int,
    tensor_parallel_size: int,
    kv_config: dict,
    max_model_len: int | None = None,
    num_gpu_blocks_override: int | None = None,
    served_model_name: str = "tier2-target",
    extra_args: list[str] | None = None,
) -> list[str]:
    if tensor_parallel_size < 1:
        raise ValueError("tensor_parallel_size must be at least 1")
    argv = [
        python,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        model,
        "--served-model-name",
        served_model_name,
        "--host",
        host,
        "--port",
        str(port),
        "--tensor-parallel-size",
        str(tensor_parallel_size),
        "--kv-transfer-config",
        _encode(kv_config),
    ]
    if max_model_len is not None:
        argv += ["--max-model-len", str(max_model_len)]
    if num_gpu_blocks_override is not None:
        argv += ["--num-gpu-blocks-override", str(num_gpu_blocks_override)]
    if extra_args:
        argv += list(extra_args)
    return argv


def build_plan(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out).resolve()
    kv_config = kv_transfer_config(
        args.cpu_bytes_to_use,
        spec_name=args.spec_name,
        eviction_policy=args.eviction_policy,
        canonical_layout=not args.no_canonical_layout,
        block_size=args.block_size,
    )
    argv = server_argv(
        args.python,
        model=args.model,
        host=args.host,
        port=args.port,
        tensor_parallel_size=args.tensor_parallel_size,
        kv_config=kv_config,
        max_model_len=args.max_model_len,
        num_gpu_blocks_override=args.num_gpu_blocks_override,
        served_model_name=args.served_model_name,
        extra_args=args.server_arg,
    )
    return {
        "out_dir": str(out_dir),
        "argv": argv,
        "env": plugin_environment(
            out_dir, args.evidence_label, descriptor_capture=not args.no_descriptors
        ),
        "kv_transfer_config": kv_config,
        "base_url": f"http://{args.host}:{args.port}",
        "workload": {
            "requests": args.requests,
            "prompt_chars": args.prompt_chars,
            "pressure_requests": args.pressure_requests,
            "pressure_chars": args.pressure_chars,
            "concurrency": args.concurrency,
            "reset_gpu_prefix_cache": not args.no_reset_prefix_cache,
        },
    }


def probe_target(python: str, timeout: float) -> dict:
    """Ask the target interpreter for the exact host and plugin contract."""

    try:
        completed = subprocess.run(
            [python, "-c", PROBE_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": type(exc).__name__, "detail": str(exc)[:400]}
    if completed.returncode != 0:
        return {
            "error": "probe_failed",
            "detail": (completed.stderr or "").strip()[-400:],
        }
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {"error": "probe_unparsed", "detail": (completed.stdout or "")[-400:]}


def preflight_checks(probe: dict) -> list[dict]:
    """Fail closed on any contract mismatch; never widen the host claim."""

    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append(
            {"name": name, "status": "pass" if ok else "fail", "detail": detail}
        )

    if probe.get("error"):
        record(
            "target_probe",
            False,
            f"target interpreter probe failed: {probe.get('error')}",
        )
        return checks
    record("target_probe", True, f"python {probe.get('python')}")

    plugin_version = probe.get("plugin_version")
    record(
        "plugin_installed",
        bool(plugin_version),
        f"plugin {plugin_version}" if plugin_version else "plugin not importable",
    )

    required = probe.get("plugin_required_host_api")
    actual = probe.get("host_observability_api")
    record(
        "host_observability_api",
        required is not None and actual == required,
        f"host={actual!r} plugin_requires={required!r}",
    )

    correlated_required = probe.get("plugin_correlated_host_api")
    correlated_actual = probe.get("host_correlated_api")
    record(
        "host_correlated_api",
        correlated_required is not None and correlated_actual == correlated_required,
        f"host={correlated_actual!r} plugin_requires={correlated_required!r}",
    )
    return checks


def _open(
    url: str,
    payload: object = None,
    headers: dict | None = None,
    timeout: float = 60.0,
    method: str | None = None,
):
    data = None if payload is None else _encode(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers=dict(headers or {}), method=method
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def wait_healthy(
    base_url: str, process: subprocess.Popen, log_path: Path, deadline_seconds: float
) -> None:
    end = time.monotonic() + deadline_seconds
    while time.monotonic() < end:
        if process.poll() is not None:
            raise DriverError(
                f"server exited with {process.returncode} before /health; "
                f"see {log_path}"
            )
        try:
            status, _ = _open(f"{base_url}/health", timeout=5.0)
            if status == 200:
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2.0)
    raise DriverError(f"server did not become healthy within {deadline_seconds}s")


def build_prompt(index: int, chars: int) -> str:
    filler = "the quick brown fox jumps over the lazy dog. "
    seed = f"tier2-{index:04d} "
    repeats = chars // len(filler) + 1
    return (seed + filler * repeats)[: max(chars, len(seed))]


def send_completion(
    base_url: str,
    model: str,
    prompt: str,
    request_id: str,
    max_tokens: int,
    timeout: float,
) -> dict:
    """Send one completion under an explicit engine request id."""

    try:
        status, body = _open(
            f"{base_url}/v1/completions",
            {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            {"X-Request-Id": request_id},
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        return {"request_id": request_id, "status": exc.code, "ok": False}
    except (urllib.error.URLError, OSError) as exc:
        return {
            "request_id": request_id,
            "status": None,
            "ok": False,
            "error": type(exc).__name__,
        }
    return {
        "request_id": request_id,
        "status": status,
        "ok": status == 200,
        "bytes": len(body),
    }


def reset_prefix_cache(base_url: str, deadline_seconds: float) -> bool:
    """Drop the GPU prefix cache while keeping the connector's CPU copies."""

    end = time.monotonic() + deadline_seconds
    while True:
        try:
            status, body = _open(
                f"{base_url}/reset_prefix_cache?reset_external=false",
                method="POST",
                timeout=60.0,
            )
            if status == 200 and json.loads(body or b"{}").get("success"):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(1.0)


def summarize_events(path: Path) -> dict:
    """Count canonical events without trusting file names or ordering."""

    counts: dict[str, int] = {}
    request_ids: set[str] = set()
    malformed = 0
    if not path.is_file():
        return {"records": 0, "malformed": 0, "counts": {}, "request_ids": []}
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            event = record.get("event")
            direction = (record.get("identity") or {}).get("direction")
            key = f"{event}/{direction}" if direction else str(event)
            counts[key] = counts.get(key, 0) + 1
            identity = record.get("identity")
            if isinstance(identity, dict):
                request_id = identity.get("request_id")
                if isinstance(request_id, str):
                    request_ids.add(request_id)
    return {
        "records": sum(counts.values()),
        "malformed": malformed,
        "counts": dict(sorted(counts.items())),
        "request_ids": sorted(request_ids),
    }


def run_validation(
    python: str,
    events: Path,
    capture_dir: Path | None,
    expect_chain: str | None,
    timeout: float,
) -> dict:
    argv = [python, "-m", VALIDATION_MODULE, "--events", str(events)]
    if capture_dir is not None:
        argv += ["--capture-dir", str(capture_dir)]
    if expect_chain is not None:
        argv += ["--expect-restore-chain", expect_chain]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": type(exc).__name__, "argv": argv}
    payload: object = None
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": payload,
        "stderr": (completed.stderr or "").strip()[-400:],
        "expect_restore_chain": expect_chain,
        "argv": argv[1:],
    }


def digest(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "present": False}
    size = path.stat().st_size
    if size > MAX_SHA256_BYTES:
        return {
            "path": str(path),
            "present": True,
            "bytes": size,
            "sha256": None,
            "note": "exceeds digest limit",
        }
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            checksum.update(chunk)
    return {
        "path": str(path),
        "present": True,
        "bytes": size,
        "sha256": checksum.hexdigest(),
    }


def teardown_observations(log_path: Path) -> dict:
    text = ""
    if log_path.is_file():
        text = log_path.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    return {
        "force_kill": "force killing" in lowered,
        "resource_tracker": "resource_tracker" in lowered,
        "abort_timeout_zero": "timeout=0s" in lowered,
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", required=True, help="target venv Python holding the matched host"
    )
    parser.add_argument("--model", required=True, help="local model path")
    parser.add_argument(
        "--out", required=True, help="evidence directory (created if missing)"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8187)
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--served-model-name", default="tier2-target")
    parser.add_argument("--max-model-len", type=int)
    parser.add_argument(
        "--num-gpu-blocks-override",
        type=int,
        help="shrink the GPU KV pool so pressure is reachable",
    )
    parser.add_argument("--cpu-bytes-to-use", type=int, default=8 << 30)
    parser.add_argument("--spec-name", default="CPUOffloadingSpec")
    parser.add_argument("--eviction-policy", default="lru")
    parser.add_argument("--block-size", type=int)
    parser.add_argument(
        "--no-canonical-layout",
        action="store_true",
        help="disable the descriptor capture layout (fails Tier 2)",
    )
    parser.add_argument("--no-descriptors", action="store_true")
    parser.add_argument(
        "--evidence-label", default="real-online", choices=EVIDENCE_LABELS
    )
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--prompt-chars", type=int, default=6000)
    parser.add_argument("--pressure-requests", type=int, default=6)
    parser.add_argument("--pressure-chars", type=int, default=16000)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--no-reset-prefix-cache", action="store_true")
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--server-start-timeout", type=float, default=900.0)
    parser.add_argument("--probe-timeout", type=float, default=180.0)
    parser.add_argument("--shutdown-grace", type=float, default=30.0)
    parser.add_argument("--validation-timeout", type=float, default=600.0)
    parser.add_argument("--require-clean-teardown", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the exact plan and exit"
    )
    parser.add_argument(
        "--server-arg",
        action="append",
        default=[],
        help="extra argv appended to the server command",
    )
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> int:
    out_dir = Path(args.out).resolve()
    plan = build_plan(args)
    if args.dry_run:
        print(_encode({"mode": "dry-run", "plan": plan}))
        return 0
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        print(
            _encode(
                {
                    "outcome": "output_not_empty",
                    "out_dir": str(out_dir),
                    "detail": "refusing to mix this run with existing evidence",
                }
            )
        )
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    descriptor_dir = None if args.no_descriptors else out_dir / "descriptors"
    log_path = out_dir / "server.log"

    checks: list[dict] = []
    probe = probe_target(args.python, args.probe_timeout)
    write_json(out_dir / "preflight.json", probe)
    checks += preflight_checks(probe)
    run_record: dict[str, object] = {
        "plan": plan,
        "probe": probe,
        "checks": checks,
        "scope": (
            "single-run Tier 2 evidence on one qualified pair; "
            "no latency or throughput claim"
        ),
    }
    if any(check["status"] == "fail" for check in checks):
        run_record["outcome"] = "preflight_failed"
        write_json(out_dir / "manifest.json", run_record)
        print(_encode({"outcome": "preflight_failed", "checks": checks}))
        return 2

    environment = dict(os.environ)
    environment.update(plan["env"])
    if descriptor_dir is not None:
        descriptor_dir.mkdir(parents=True, exist_ok=True)
    sent: list[str] = []
    process = None
    log_stream = None
    try:
        log_stream = log_path.open("wb")
        popen_kwargs: dict[str, object] = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            plan["argv"],
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            env=environment,
            **popen_kwargs,
        )
        wait_healthy(plan["base_url"], process, log_path, args.server_start_timeout)
        checks.append(
            {
                "name": "server_healthy",
                "status": "pass",
                "detail": f"pid {process.pid} answered /health",
            }
        )

        base_url = plan["base_url"]
        model = args.served_model_name
        prompts = [build_prompt(i, args.prompt_chars) for i in range(args.requests)]
        store_results = []
        for index, prompt in enumerate(prompts):
            request_id = f"tier2-store-{index:02d}"
            result = send_completion(
                base_url, model, prompt, request_id, 8, args.request_timeout
            )
            sent.append(request_id)
            store_results.append(result)
        checks.append(
            {
                "name": "store_phase_answered",
                "status": "pass" if all(r["ok"] for r in store_results) else "fail",
                "detail": f"{sum(1 for r in store_results if r['ok'])}/"
                f"{len(store_results)} completions returned 200",
            }
        )

        if not args.no_reset_prefix_cache:
            reset_ok = reset_prefix_cache(base_url, args.request_timeout)
            checks.append(
                {
                    "name": "gpu_prefix_cache_reset",
                    "status": "pass" if reset_ok else "fail",
                    "detail": "GPU prefix cache dropped, CPU copies retained"
                    if reset_ok
                    else "reset_prefix_cache never reported success",
                }
            )

        reload_results = []
        for index, prompt in enumerate(prompts):
            request_id = f"tier2-reload-{index:02d}"
            result = send_completion(
                base_url, model, prompt, request_id, 8, args.request_timeout
            )
            sent.append(request_id)
            reload_results.append(result)
        checks.append(
            {
                "name": "reload_phase_answered",
                "status": "pass" if all(r["ok"] for r in reload_results) else "fail",
                "detail": f"{sum(1 for r in reload_results if r['ok'])}/"
                f"{len(reload_results)} reloads returned 200",
            }
        )

        pressure_prompts = [
            build_prompt(1000 + i, args.pressure_chars)
            for i in range(args.pressure_requests)
        ]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.concurrency)
        ) as pool:
            futures = []
            for index, prompt in enumerate(pressure_prompts):
                request_id = f"tier2-pressure-{index:02d}"
                sent.append(request_id)
                futures.append(
                    pool.submit(
                        send_completion,
                        base_url,
                        model,
                        prompt,
                        request_id,
                        8,
                        args.request_timeout,
                    )
                )
            pressure_results = [future.result() for future in futures]
        answered = sum(1 for r in pressure_results if r["ok"])
        checks.append(
            {
                "name": "pressure_phase_answered",
                "status": "pass" if answered else "fail",
                "detail": f"{answered}/{len(pressure_results)} concurrent "
                "completions returned 200",
            }
        )
    except DriverError as exc:
        checks.append(
            {"name": "server_run", "status": "fail", "detail": str(exc)[:400]}
        )
    finally:
        if process is not None and process.poll() is None:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
            else:
                process.terminate()
            try:
                exit_code = process.wait(timeout=args.shutdown_grace)
                stop_detail = f"SIGINT exit {exit_code} within {args.shutdown_grace}s"
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
                exit_code = process.wait(timeout=60)
                stop_detail = (
                    f"SIGKILL exit {exit_code} after SIGINT exceeded "
                    f"{args.shutdown_grace}s"
                )
            checks.append(
                {
                    "name": "server_stopped",
                    "status": "pass",
                    "detail": stop_detail,
                }
            )
        if log_stream is not None:
            log_stream.close()

    observations = teardown_observations(log_path)
    if args.require_clean_teardown:
        clean = not observations["force_kill"] and not observations["resource_tracker"]
        checks.append(
            {
                "name": "clean_teardown",
                "status": "pass" if clean else "fail",
                "detail": _encode(observations),
            }
        )

    summary = summarize_events(events_path)
    checks.append(
        {
            "name": "d2h_store_events",
            "status": "pass"
            if summary["counts"].get("preserve_started/d2h")
            else "fail",
            "detail": _encode(summary["counts"]),
        }
    )
    checks.append(
        {
            "name": "h2d_restore_events",
            "status": "pass"
            if summary["counts"].get("restore_started/h2d")
            else "fail",
            "detail": "restore path observed"
            if summary["counts"].get("restore_started/h2d")
            else "no restore event",
        }
    )

    if descriptor_dir is not None:
        inventories = len(list(descriptor_dir.glob("*.json")))
        checks.append(
            {
                "name": "descriptor_inventories",
                "status": "pass" if inventories else "fail",
                "detail": f"{inventories} inventory files",
            }
        )

    chains: list[dict] = []
    if summary["records"]:
        candidates = [rid for rid in sent if rid in set(summary["request_ids"])]
        for request_id in candidates:
            outcome = run_validation(
                args.python,
                events_path,
                descriptor_dir,
                request_id,
                args.validation_timeout,
            )
            chains.append(outcome)
            if outcome["ok"]:
                break
    complete = [chain for chain in chains if chain["ok"]]
    checks.append(
        {
            "name": "complete_restore_chain",
            "status": "pass" if complete else "fail",
            "detail": (
                f"validated chain for {complete[0].get('expect_restore_chain')}"
                if complete
                else f"no complete chain among {len(chains)} candidate request ids"
            ),
        }
    )

    failed = [check for check in checks if check["status"] == "fail"]
    run_record.update(
        {
            "checks": checks,
            "sent_request_ids": sent,
            "event_summary": summary,
            "validation": chains,
            "teardown_observations": observations,
            "artifacts": [
                digest(path) for path in sorted(out_dir.glob("*")) if path.is_file()
            ],
            "outcome": "passed" if not failed else "failed",
        }
    )
    write_json(out_dir / "manifest.json", run_record)
    print(
        _encode(
            {
                "outcome": run_record["outcome"],
                "out_dir": str(out_dir),
                "failed": [check["name"] for check in failed],
                "events": summary["records"],
                "descriptor_dir": str(descriptor_dir) if descriptor_dir else None,
                "scope": run_record["scope"],
            }
        )
    )
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    return execute(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
