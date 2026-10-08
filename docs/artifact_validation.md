# Offline v2 artifact validation

Use the installed package's schema and lifecycle checks before treating a
capture as usable input. This validator accepts the canonical v2 event and
region-descriptor formats emitted by this package. It does not accept the old
v1 vocabulary, or translate raw OffloadingConnector observations into missing
worker/process identities.

```bash
python -m vllm_hust_kv_transfer_observability.validation \
  --events /path/to/events.jsonl \
  --capture-dir /path/to/descriptors \
  --expect-restore-chain REQUEST_ID
```

The command returns 1 on invalid/missing artifacts and 2 for invalid CLI
arguments. Unknown fields, malformed identities, duplicate JSON keys, invalid
numbers, empty captures and v1 input fail regardless of whether the chain flag
is supplied. JSON is reconstructed through the same typed classes used by
the writers; arbitrary address/payload fields cannot bypass validation.
This is structural validation, not a way to prove that an allowed opaque
request identifier contains no sensitive application data.

Without `--expect-restore-chain`, event validation is schema-only. With it,
every observed recovery episode of that request must contain requeue, all
restore submissions and completions, exact-roster admission and first compute.
The validator reuses `LifecycleNormalizer` and checks request, generation,
rank, epoch, process-scoped transfer/receipt identities, ordering, completion
measurements, and duplicate/reused receipts. A valid store or an isolated
restore does not satisfy this requirement. Each worker/epoch is checked
independently; clocks from different workers are never compared. Files must
retain causal order within an episode. Failed, cancelled or partial recovery
slices are rejected for this *successful complete chain* check; they remain
valid diagnostic input for schema-only checks.

When `--capture-dir` is supplied, each inventory must have nonempty typed v2
regions and a transfer/worker identity and direction present in the event
file. Duplicate inventories fail. Correlation uses explicit fields, not file
names or timestamp proximity. This checks supplied inventories; it does not
prove that all expected transfers or workers were captured. Event v2 has no
job-id field, so this tool cannot independently validate descriptor job-id
association beyond its type. It also cannot attest a hardware-specific region
layout, dropped records outside the file, or an evidence label's truth.

Inputs are bounded to 1 MiB per record/inventory and 64 MiB / 100,000 records
per artifact set. The existing normalizer's 4096 concurrent-transfer and
admission bounds still apply. Exceeding a limit fails validation; records are
never silently dropped. Preserve the original artifact and investigate the
scope instead of trimming a failing file until it passes.

All regression fixtures are synthetic conformance evidence. Passing this
command does not establish complete worker coverage, device execution,
compatibility, or performance. Manifest 0.3 activates only worker-local
submit/complete/cancel observations. The complete restore-chain check is for
future correlated captures; current native output cannot satisfy it because
the plugin does not yet attach scheduler recovery and first-compute records.
Core PR #46 has merged and publishes its observer API through the host
capability registry, but the plugin-visible cross-process identity remains
insufficient for a complete recovery receipt.

This implementation addresses the stale-schema and false-positive findings
in the proposed toolkit PR #9. That PR has not been merged here: use this
package command instead of its older `verify_events.py`. Its package/device
scripts and the operational claims in PR #8 need separate review.
