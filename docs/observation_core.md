# Typed bounded observation core

This document describes the host-independent Phase 2 implementation. It is not
a host attachment, compatibility claim, activation guide, or performance
result.

## Closed event model

`KVTransferObservation` is immutable and accepts only `ObservationEvent` values:

| Event | Required event-specific fields |
|---|---|
| `preserve_started` | transfer, `d2h`, block count |
| `preserve_completed` | transfer, `d2h`, bytes, block count, duration |
| `transfer_started` | transfer, direction, bytes |
| `transfer_completed` | transfer, direction, bytes, duration |
| `transfer_failed` | transfer, direction, closed failure reason |
| `transfer_cancelled` | transfer, direction, closed cancellation reason |
| `restore_started` | transfer, `h2d`, block count |
| `restore_completed` | transfer, `h2d`, same-process receipt, bytes, block count, duration |
| `recovery_requeued` | recovery epoch and closed requeue reason |
| `recovery_admitted` | recovery epoch and sorted unique transfer associations |
| `first_compute` | recovery epoch, compute kind, same-process receipt and transfer associations |

Unknown events, unknown fields, arbitrary error strings, raw addresses, device
pointers, and KV payloads have no representation in the schema.

Completed preserve, transfer, and restore observations may additionally carry
`device_duration_ns`. It is kept distinct from the host-observed
`duration_ns`; neither value is inferred from the other.

Every record carries a printable ASCII request ID of at most 128 bytes, a
uint64 worker generation, uint32 rank, optional positive uint64 recovery epoch,
and uint64 monotonic timestamp. Transfer and receipt IDs retain the merged
legacy process-scoped forms `process_uuid:t:sequence` and
`process_uuid:k:sequence`. First-compute and restore receipts must share a
process scope with the transfer IDs they reference. Association lists are
sorted, unique, immutable, and limited to 4096 entries.

## Event sink behavior

`JsonlKVTransferEventSink` has these implementation defaults:

| Boundary | Default |
|---|---|
| pending queue | 4096 records |
| serialized record | 1 MiB |
| output file | 64 MiB |

The values are constructor parameters with strict positive/bounds validation;
they are implementation defaults, not performance targets.

Calls to `emit()` serialize before queue admission and never perform file I/O.
The bounded worker queue uses non-blocking admission. The writer uses an
explicit destination, `O_NOFOLLOW`, a pinned directory descriptor, append-mode
file locking, complete-write loops, and rollback to the previous file length
after a partial write failure. When the file limit is reached, subsequent
records are dropped; this phase intentionally does not rotate or overwrite
evidence.

`EventSinkCounters` reports enqueued, written, queue-dropped,
record-size-dropped, file-capacity-dropped, serialization-error, I/O-error,
closed-drop, and shutdown-timeout counts.

## Descriptor capture behavior

`DescriptorInventory` requires the same request/generation/rank/recovery and
transfer identity plus a uint64 job ID, direction, timestamp, and a nonempty
tuple of at most 4096 `DescriptorRegion` values. A region contains only uint64
source offset, destination offset, positive size, and direction.

`DescriptorLayoutCapture` rejects a symlink or invalid directory during
construction, pins the directory descriptor and identity, creates a mode-0600
temporary file relative to that descriptor, completes and fsyncs the write,
and publishes with a no-overwrite hard link. Conflicts and all runtime I/O
failures return `None`. Failed publications perform best-effort cleanup and
count any cleanup failure; no partial file is intentionally published.
Concurrent capture/close is serialized so closing cannot invalidate an active
atomic publication.

`DescriptorCaptureCounters` reports written, invalid, capacity-dropped,
conflict, I/O-error, and closed-drop counts.

## Failure boundary

- Invalid schema objects and invalid constructor configuration raise before
  runtime attachment. This is the fail-closed validation boundary.
- After construction, `emit()` and `capture()` report failure through their
  return value and counters. Filesystem loss, short writes, full queues,
  capacity exhaustion, conflicts, symlinks, and close races do not propagate
  into real KV transfer or serving. This is the runtime fail-open boundary.
- The manifest remains `import_only`; none of this code registers a host
  callback or activates on installation.
