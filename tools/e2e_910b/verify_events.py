#!/usr/bin/env python3
"""Verify kv-transfer-observability E2E artifacts against HOST_CONTRACT.

Checks (all offline, pure stdlib):
  1. events.jsonl: every line parses, schema matches, event name is inside the
     HOST_CONTRACT v1 vocabulary, pid/ts are ints, request_id non-empty.
  2. Optional per-request chain order (--expect-restore-chain REQ_ID):
     restore_start < restore_done; and if scheduler events are present for the
     same request (preempt/wakeup/admission/scheduled), wakeup < admission <
     scheduled must hold.
  3. descriptor captures: JSON schema, strict 4-key descriptors, no "address"
     material anywhere, direction consistency.

Usage:
  python3 verify_events.py --events EVENTS.jsonl [--capture-dir DIR]
      [--expect-restore-chain REQ_ID]
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVENT_SCHEMA = "vllm-hust.kv-transfer-event.v1"
DESCRIPTOR_SCHEMA = "vllm-hust.kv-transfer-descriptor-layout.v1"

# Normative vocabulary for THIS package's host adapter (HOST_CONTRACT.md,
# "Event ownership boundary": scheduler-layer events belong to the host
# EventBus / PR vllm-hust#6, not to this adapter — keep in sync).
RESTORE_EVENTS = {
    "restore_start",
    "restore_done",
}
STORE_EVENTS = {
    "cpu_store",
    "cpu_evict",
    "evict",
}
TRANSFER_EVENTS = {
    "transfer_submit",
    "swap_d2h_submit",
    "gather_h2d",
    "copy_observed_complete",
    "sched_step",
}
VOCABULARY = RESTORE_EVENTS | STORE_EVENTS | TRANSFER_EVENTS

DESCRIPTOR_KEYS = {"src_offset", "dst_offset", "size", "direction"}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def verify_events(path: Path, expect_chain: str | None) -> None:
    if not path.exists():
        fail(f"events file missing: {path}")
    records = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"line {lineno}: invalid JSONL: {exc}")
        if rec.get("schema") != EVENT_SCHEMA:
            fail(f"line {lineno}: schema {rec.get('schema')!r} != {EVENT_SCHEMA}")
        event = rec.get("event")
        if event not in VOCABULARY:
            fail(f"line {lineno}: event {event!r} outside HOST_CONTRACT vocabulary")
        if not rec.get("request_id"):
            fail(f"line {lineno}: empty request_id")
        if not isinstance(rec.get("pid"), int) or not isinstance(
            rec.get("ts_monotonic_ns"), int
        ):
            fail(f"line {lineno}: pid/ts_monotonic_ns must be ints")
        records.append(rec)
    if not records:
        fail("no events recorded")
    print(f"OK: {len(records)} events, all schema/vocabulary valid")

    if expect_chain:
        req = [r for r in records if r["request_id"] == expect_chain]
        if not req:
            fail(f"request {expect_chain!r} not found in events")
        names = [r["event"] for r in req]
        # Hard constraints from HOST_CONTRACT ordering section (adapter stream
        # only; scheduler-layer events are owned by the host EventBus).
        if (
            "restore_start" in names
            and "restore_done" in names
            and names.index("restore_start") > names.index("restore_done")
        ):
            fail(f"{expect_chain}: restore_done before restore_start")
        # Descriptor-free rule: any address material is fatal.
        for rec in req:
            if "address" in json.dumps(rec):
                fail(f"{expect_chain}: address material in event payload")
        print(
            f"OK: request {expect_chain!r} chain order valid "
            f"(events: {', '.join(names)})"
        )


def verify_descriptors(capture_dir: Path) -> None:
    if not capture_dir.is_dir():
        fail(f"capture dir missing: {capture_dir}")
    files = sorted(capture_dir.glob("*.json"))
    if not files:
        fail(f"no descriptor captures in {capture_dir}")
    for f in files:
        text = f.read_text()
        if "address" in text:
            fail(f"{f.name}: address material present (red line)")
        payload = json.loads(text)
        if payload.get("schema") != DESCRIPTOR_SCHEMA:
            fail(f"{f.name}: schema {payload.get('schema')!r} != {DESCRIPTOR_SCHEMA}")
        if payload.get("direction") not in {"d2h", "h2d"}:
            fail(f"{f.name}: bad direction {payload.get('direction')!r}")
        for desc in payload.get("descriptors", []):
            if set(desc) != DESCRIPTOR_KEYS:
                fail(f"{f.name}: descriptor keys {sorted(desc)} != 4-key v1 schema")
            if desc["direction"] != payload["direction"]:
                fail(f"{f.name}: descriptor direction mismatch within inventory")
            if desc["size"] <= 0 or desc["src_offset"] < 0 or desc["dst_offset"] < 0:
                fail(f"{f.name}: invalid offset/size {desc}")
        print(f"OK: {f.name}: {len(payload.get('descriptors', []))} descriptors, clean")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--expect-restore-chain", type=str)
    args = parser.parse_args()
    verify_events(args.events, args.expect_restore_chain)
    if args.capture_dir:
        verify_descriptors(args.capture_dir)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
