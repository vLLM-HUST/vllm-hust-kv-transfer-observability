# SPDX-License-Identifier: Apache-2.0
"""Check opt-in wheel install/uninstall in a disposable, offline environment.

This is package removal evidence, not live observer disable/rollback evidence.
Run after building: python tools/check_wheel_uninstall.py dist/*.whl
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "vllm-hust-kv-transfer-observability"
MODULE = "vllm_hust_kv_transfer_observability"
ENTRY_POINTS = (
    ("vllm_hust.extension_bundles", "org.vllm-hust.kv-transfer-observability"),
    ("vllm.general_plugins", "kv_transfer_observability"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    if wheel.suffix != ".whl":
        parser.error("expected a built wheel")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PIP_CONFIG_FILE"] = os.devnull
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    with tempfile.TemporaryDirectory(prefix="kv-uninstall-") as directory:
        work = Path(directory)
        python = (
            work / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )

        def run(*command: str) -> None:
            subprocess.run(command, cwd=work, env=env, check=True, timeout=120)

        run(sys.executable, "-I", "-m", "venv", str(work / "env"))

        def check(installed: bool) -> None:
            run(
                str(python),
                "-I",
                "-c",
                "import importlib.util, importlib.metadata as m; "
                f"pairs = {ENTRY_POINTS!r}; "
                "entries = tuple(tuple(m.entry_points(group=g, name=n)) "
                "for g, n in pairs); "
                f"assert all(len(items) == {int(installed)} for items in entries), "
                "entries; "
                f"present = importlib.util.find_spec({MODULE!r}) is not None; "
                f"assert present == {installed!r}",
            )

        check(False)
        run(
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(wheel),
        )
        check(True)
        run(
            str(python),
            "-I",
            "-c",
            "import sys, threading; before = set(threading.enumerate()); "
            f"import {MODULE}; "
            "assert set(threading.enumerate()) == before; "
            "assert 'vllm' not in sys.modules",
        )
        # Existing operator-owned output must survive package removal.
        evidence = work / "retained-evidence.jsonl"
        evidence.write_text('{"fixture": "operator-owned"}\n', encoding="utf-8")
        for _ in range(2):
            run(str(python), "-I", "-m", "pip", "uninstall", "-y", PACKAGE)
            check(False)
            assert (
                evidence.read_text(encoding="utf-8")
                == '{"fixture": "operator-owned"}\n'
            )
        print(
            "PASS: isolated wheel discovery/removal, repeat uninstall, "
            "retained evidence"
        )
        print(
            "Scope: package removal without explicit activation; "
            "no live host activation or worker cleanup tested"
        )


if __name__ == "__main__":
    main()
