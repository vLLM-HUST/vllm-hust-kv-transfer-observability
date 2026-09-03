# Lifecycle normalization and correlation

This document describes the first host-independent Phase 3 implementation. It
does not activate the extension, import a host runtime, register callbacks, or
claim current-host compatibility.

## Source scope

The implemented source records are limited to semantics present in the merged
legacy sources recorded in `PROVENANCE.md`:

- core PR #236: submitted and completed D2H preserve/H2D restore attempts,
  explicit transfer invalidation, recovery requeue/admission, H2D receipts,
  and exact-roster first-compute observation;
- Ascend PR #216: optional forwarding of that same first-compute observation
  immediately before real model forward when the paired core exposes the
  callback.

Connector job allocation, KV ownership/copying, scheduling, model forward, and
callback registration remain host-owned. The normalizer never imports those
implementations.

## Mapping

| Typed source observation | Canonical observation |
|---|---|
| submitted D2H preserve | `preserve_started` |
| successful D2H completion | `preserve_completed` |
| submitted H2D restore | `restore_started` |
| successful H2D receipt | `restore_completed` |
| unsuccessful backend completion | `transfer_failed` |
| explicit discard/invalidation | `transfer_cancelled` |
| recovery requeue | `recovery_requeued` |
| admission with an exact completed-restore roster | `recovery_admitted` |
| exact admitted roster before core or Ascend model forward | `first_compute` |

The source host name is not part of the canonical result: core and Ascend
first-compute forwarding normalize to the same record. Identity is carried by
explicit typed fields and is never inferred from filenames, event proximity,
or timing.

### Legacy B134 vocabulary

`b134.py` records the exact 14-event vocabulary from merged core PR #220 as a
closed enum and immutable contract table. The original ownership split is four
scheduler events and ten KV-offload events. First compute came later from core
#236/Ascend #216, so the combined source vocabulary contains 15 events; it is
not part of the original B134 count.

Only four B134 names have a direct semantic correspondence with the canonical
model:

| B134 event | Canonical event |
|---|---|
| `restore_start` | `restore_started` |
| `restore_done` | `restore_completed` |
| `transfer_submit` | `transfer_started` |
| `copy_observed_complete` | `transfer_completed` |

The other ten events remain explicitly classified scheduler or supplemental
phase measurements. They are not relabelled as lifecycle transitions because
that would manufacture identity or semantics absent from the source. A future
host adapter may forward them through a separately defined measurement schema,
but this Phase 3 work does not invent that schema.

Even the four corresponding names cannot construct a canonical record from the
legacy payload alone: B134 embedded a job number in `request_id` and did not
carry the bounded transfer/receipt/generation identity required by v2. The
adapter must combine the event with the explicit core #236 identity sidecar;
the plugin never guesses that association from filenames or timestamps. The
legacy six-event recovery order is recorded as `B134_RECOVERY_CHAIN`.

Host-observed elapsed time and optional device-event elapsed time remain
separate as `duration_ns` and `device_duration_ns`. The latter is accepted only
on completed transfer records.

## Bounded state and failure behavior

`LifecycleNormalizer` bounds pending/completed transfer correlation and pending
recovery admissions. Unknown, duplicate, out-of-order, or malformed source
observations return `None` and increment counters instead of raising into a
serving caller.

Capacity loss and a first-compute roster mismatch make subsequent formal
evidence unusable (`evidence_valid == False`) while remaining fail-open for
serving. Closing is idempotent, clears correlation state, and causes later
observations to be counted and dropped.

## Remaining Phase 3 work

- add the adapter-facing translation from exact current-host callback objects
  only after Phase 4 verifies that those callbacks really exist.

The B134 vocabulary is now reconciled and descriptor v2 uses bounded numeric
source/destination region IDs derived from the legacy tensor index. Real host
translation remains Phase 4 and does not authorize changing the manifest from
`import_only`.
