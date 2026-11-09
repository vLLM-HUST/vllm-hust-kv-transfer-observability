from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

DRIVER_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "e2e_910b" / "03_tier2_driver.py"
)
_SPEC = importlib.util.spec_from_file_location("tier2_driver", DRIVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
driver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(driver)


def _plan(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--python",
        "/venv/bin/python",
        "--model",
        "/models/target",
        "--out",
        str(tmp_path),
        *extra,
    ]


def test_kv_transfer_config_requires_a_positive_cpu_budget() -> None:
    config = driver.kv_transfer_config(1 << 30)
    extra = config["kv_connector_extra_config"]
    assert config["kv_connector"] == "OffloadingConnector"
    assert extra["canonical_layout"] is True
    assert extra["cpu_bytes_to_use"] == 1 << 30
    for value in (0, -1, True):
        with pytest.raises(ValueError):
            driver.kv_transfer_config(value)


def test_plugin_environment_keeps_activation_explicit(tmp_path: Path) -> None:
    environment = driver.plugin_environment(tmp_path, "real-online")
    assert environment[driver.ENABLED_BUNDLES_ENV] == driver.EXTENSION_ID
    assert environment[driver.EVENT_PATH_ENV] == str(tmp_path / "events.jsonl")
    assert environment[driver.DESCRIPTOR_DIR_ENV] == str(tmp_path / "descriptors")
    assert environment[driver.EVIDENCE_LABEL_ENV] == "real-online"
    without = driver.plugin_environment(
        tmp_path, "real-online", descriptor_capture=False
    )
    assert driver.DESCRIPTOR_DIR_ENV not in without
    assert driver.EVIDENCE_LABEL_ENV not in without
    with pytest.raises(ValueError):
        driver.plugin_environment(tmp_path, "made-up")


def test_server_argv_carries_the_exact_connector_config(tmp_path: Path) -> None:
    config = driver.kv_transfer_config(2 << 30)
    argv = driver.server_argv(
        "/venv/bin/python",
        model="/models/target",
        host="127.0.0.1",
        port=8187,
        tensor_parallel_size=2,
        kv_config=config,
        max_model_len=8192,
        num_gpu_blocks_override=64,
    )
    assert argv[argv.index("--tensor-parallel-size") + 1] == "2"
    assert argv[argv.index("--num-gpu-blocks-override") + 1] == "64"
    encoded = json.loads(argv[argv.index("--kv-transfer-config") + 1])
    assert encoded == config


def test_preflight_fails_closed_on_a_host_api_mismatch() -> None:
    probe = {
        "python": "3.12.9",
        "plugin_version": "0.3.0",
        "plugin_required_host_api": "1.1",
        "plugin_correlated_host_api": "2.0",
        "host_observability_api": "1.0",
        "host_correlated_api": "2.0",
    }
    outcomes = {
        check["name"]: check["status"] for check in driver.preflight_checks(probe)
    }
    assert outcomes["host_observability_api"] == "fail"
    assert outcomes["plugin_installed"] == "pass"

    probe["host_observability_api"] = "1.1"
    outcomes = {
        check["name"]: check["status"] for check in driver.preflight_checks(probe)
    }
    assert all(status == "pass" for status in outcomes.values())


def test_preflight_reports_a_probe_failure() -> None:
    checks = driver.preflight_checks({"error": "probe_failed"})
    assert checks == [
        {
            "name": "target_probe",
            "status": "fail",
            "detail": "target interpreter probe failed: probe_failed",
        }
    ]


def test_summarize_events_counts_canonical_records(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "preserve_started",
                        "identity": {"request_id": "a", "direction": "d2h"},
                    }
                ),
                json.dumps(
                    {
                        "event": "restore_started",
                        "identity": {"request_id": "a", "direction": "h2d"},
                    }
                ),
                "not json",
                json.dumps({"event": "recovery_admitted"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = driver.summarize_events(events)
    assert summary["counts"]["preserve_started/d2h"] == 1
    assert summary["counts"]["restore_started/h2d"] == 1
    assert summary["counts"]["recovery_admitted"] == 1
    assert summary["malformed"] == 1
    assert summary["records"] == 3
    assert summary["request_ids"] == ["a"]


def test_run_validation_passes_the_explicit_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = json.dumps({"events": 3, "complete_restore_episodes": 1})
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    outcome = driver.run_validation(
        "/venv/bin/python",
        tmp_path / "events.jsonl",
        tmp_path / "descriptors",
        "tier2-store-00",
        30.0,
    )
    argv = captured["argv"]
    assert argv[1:3] == ["-m", driver.VALIDATION_MODULE]
    assert argv[argv.index("--events") + 1].endswith("events.jsonl")
    assert argv[argv.index("--capture-dir") + 1].endswith("descriptors")
    assert argv[argv.index("--expect-restore-chain") + 1] == "tier2-store-00"
    assert outcome["ok"] is True
    assert outcome["stdout"]["complete_restore_episodes"] == 1


def test_digest_records_the_exact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "events.jsonl"
    artifact.write_bytes(b"records\n")
    record = driver.digest(artifact)
    assert record["present"] is True
    assert record["bytes"] == 8
    assert record["sha256"] == (
        "24c675774b3e32ef1d18074a76d7ce10de77af44d39e25f820990b4199747742"
    )
    assert driver.digest(tmp_path / "missing")["present"] is False


def test_dry_run_prints_the_plan_without_starting_a_server(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = driver.main(_plan(tmp_path, "--dry-run", "--no-descriptors"))
    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed["mode"] == "dry-run"
    plan = printed["plan"]
    assert plan["env"][driver.ENABLED_BUNDLES_ENV] == driver.EXTENSION_ID
    assert driver.DESCRIPTOR_DIR_ENV not in plan["env"]
    assert "--kv-transfer-config" in plan["argv"]
    assert not (tmp_path / "events.jsonl").exists()


def test_parse_args_requires_the_target_python() -> None:
    with pytest.raises(SystemExit):
        driver.parse_args(["--model", "/models/target", "--out", "/tmp/out"])
