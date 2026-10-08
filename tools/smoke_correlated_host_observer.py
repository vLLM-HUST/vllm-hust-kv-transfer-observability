# SPDX-License-Identifier: Apache-2.0
"""Exercise the current core v2 public seam with synthetic recovery events."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
    correlated_observability as correlated,
)
from vllm.distributed.kv_transfer.kv_connector.v1.offloading import (
    observability as host,
)

from vllm_hust_kv_transfer_observability.adapter import KVTransferHostAdapter
from vllm_hust_kv_transfer_observability.config import ObserverConfig
from vllm_hust_kv_transfer_observability.native import VllmOffloadingObserverBinding
from vllm_hust_kv_transfer_observability.validation import (
    read_events,
    validate_restore_chain,
)


def main() -> None:
    if (
        host.kv_transfer_observers_configured()
        or correlated.correlated_observers_configured()
    ):
        raise RuntimeError("run the smoke in an isolated process")
    with tempfile.TemporaryDirectory(prefix="kv-correlated-smoke-") as directory:
        path = Path(directory) / "events.jsonl"
        adapter = KVTransferHostAdapter(ObserverConfig(enabled=True, event_path=path))
        binding = VllmOffloadingObserverBinding()
        if not adapter.start(binding):
            raise RuntimeError("observer did not start")
        common = {
            "scheduler_generation": "a" * 32,
            "request_id": "smoke-request",
            "recovery_epoch": 1,
        }
        worker = correlated.WorkerReceipt(0, "b" * 32)
        roster = (correlated.JobReceipt(7, (worker,)),)
        try:
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.RECOVERY_REQUEUED, **common
            )
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.RESTORE_SUBMITTED,
                **common,
                job_id=7,
                rank=0,
                worker_generation=worker.worker_generation,
                block_count=2,
            )
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.RESTORE_COMPLETED,
                **common,
                job_id=7,
                rank=0,
                worker_generation=worker.worker_generation,
            )
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.TRANSFER_RECEIPT,
                **common,
                job_id=7,
                workers=(worker,),
            )
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.RECOVERY_ADMITTED, **common, roster=roster
            )
            correlated.emit_correlated_observation(
                correlated.CorrelatedEvent.FIRST_COMPUTE,
                **common,
                rank=0,
                worker_generation=worker.worker_generation,
                roster=roster,
                compute_kind="decode",
            )
        finally:
            if not adapter.stop():
                raise RuntimeError("observer did not stop cleanly")
        if (
            host.kv_transfer_observers_configured()
            or correlated.correlated_observers_configured()
        ):
            raise RuntimeError("observer registration survived stop")
        records = read_events(path)
        if validate_restore_chain(records, "smoke-request") != 1:
            raise RuntimeError("complete correlated chain was not recorded")
        print(
            json.dumps(
                {
                    "host_api": (
                        correlated.KV_TRANSFER_CORRELATED_OBSERVABILITY_API_VERSION
                    ),
                    "records": len(records),
                    "complete_restore_episodes": 1,
                    "scope": (
                        "synthetic host API smoke; no connector, model, "
                        "or device transfer"
                    ),
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
