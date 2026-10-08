# SPDX-License-Identifier: Apache-2.0
"""Exercise the installed vLLM observer API with synthetic lifecycle records.

This checks the public callback contract and plugin cleanup. It does not run a
connector, model, server, or device transfer.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
    observability as host,
)

from vllm_hust_kv_transfer_observability.adapter import KVTransferHostAdapter
from vllm_hust_kv_transfer_observability.config import ObserverConfig
from vllm_hust_kv_transfer_observability.native import VllmOffloadingObserverBinding


def main() -> None:
    if host.kv_transfer_observers_configured():
        raise RuntimeError("run the smoke in an isolated process")
    with tempfile.TemporaryDirectory(prefix="kv-observer-smoke-") as directory:
        event_path = Path(directory) / "events.jsonl"
        adapter = KVTransferHostAdapter(
            ObserverConfig(enabled=True, event_path=event_path),
            retain_restore_receipts=False,
        )
        binding = VllmOffloadingObserverBinding(host)
        if not adapter.start(binding):
            raise RuntimeError("observer did not start")
        try:
            host.emit_kv_recovery_requeued(
                request_id="smoke-request",
                recovery_epoch=1,
                reason=host.RecoveryRequeueReason.UNCLASSIFIED,
            )
            host.emit_kv_transfer_submitted(
                operation=host.TransferOperation.H2D_RESTORE,
                job_id=7,
                rank=0,
                request_id="smoke-request",
                block_count=2,
            )
            host.emit_kv_transfer_completed(
                operation=host.TransferOperation.H2D_RESTORE,
                job_id=7,
                rank=0,
                request_id="smoke-request",
                success=True,
                bytes_moved=4096,
                duration_ns=1000,
            )
            host.emit_kv_transfer_receipt(
                job_id=7,
                rank=None,
                request_id="smoke-request",
                ranks=(0,),
            )
            host.emit_kv_recovery_admitted(
                request_id="smoke-request", recovery_epoch=1, job_ids=(7,)
            )
            host.emit_kv_first_compute(
                request_id="smoke-request",
                recovery_epoch=1,
                job_ids=(7,),
                compute_kind=host.ComputeKind.DECODE,
                rank=0,
            )
            host.emit_kv_transfer_descriptors(
                job_id=7,
                rank=None,
                operation=host.TransferOperation.H2D_RESTORE,
                descriptors=(host.KVRegionDescriptor(0, 0, 0, 0, 4096),),
            )
        finally:
            if not adapter.stop():
                raise RuntimeError("observer did not stop cleanly")
        if host.kv_transfer_observers_configured():
            raise RuntimeError("observer registration survived stop")
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        names = [event["event"] for event in events]
        if names != [
            "recovery_requeued",
            "restore_started",
            "restore_completed",
            "transfer_receipt",
            "recovery_admitted",
            "first_compute",
        ]:
            raise RuntimeError(f"unexpected recorded events: {names!r}")
        print(
            json.dumps(
                {
                    "host_api": host.KV_TRANSFER_OBSERVABILITY_API_VERSION,
                    "recorded_events": names,
                    "observations_emitted": adapter.counters.observations_emitted,
                    "host_facts_emitted": adapter.counters.host_facts_emitted,
                    "observer_registered_after_stop": False,
                    "scope": "synthetic host API smoke; no connector or NPU execution",
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
