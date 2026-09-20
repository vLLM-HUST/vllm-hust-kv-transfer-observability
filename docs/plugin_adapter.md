# Plugin-side host binding and configuration

Status: implemented and fixture-tested against the proposed observer contract;
not attached to a current vLLM host and not activatable through Extension
Manager.

`KVTransferHostAdapter` joins a source-specific host binding to the existing
typed normalizer, bounded JSONL sink, and optional address-free descriptor
capture. It does not import vLLM, discover internal objects, monkey-patch a
connector, or infer missing identity. The source-specific binding remains the
only component that will depend on the final host API.

## Binding boundary

A binding declares the exact contract string
`vllm.kv-transfer.observer.v1` and provides `register(callbacks)` plus
`unregister(handle)`. Registration must return a non-null handle. The callbacks
accept only the closed `SourceObservation` union and `DescriptorInventory`;
arbitrary dictionaries and fields are dropped.

These are plugin-internal callback types. The source-specific binding lives in
the plugin and translates host-owned event objects into `SourceObservation`
and sanitized `DescriptorInventory` values before invoking these callbacks.
The host must not import or construct plugin classes.

The adapter is explicitly started. A disabled configuration never inspects the
binding, opens a destination, starts a thread, or registers callbacks. A
contract mismatch, invalid destination, or registration failure aborts before
the adapter becomes active. Events racing with registration are dropped until
a removable handle exists.

After activation, callback, normalizer, serialization, filesystem, descriptor,
unregistration, and cleanup failures are contained and counted. `stop()` first
makes callbacks inert, then unregisters and closes plugin-owned resources.
Repeated start/stop calls do not create duplicate registration or cleanup.

Activation errors have stable subclasses of `AdapterActivationError`:
`AdapterContractError` for an unreadable or incompatible contract,
`AdapterResourceError` for resource initialization, and
`AdapterRegistrationError` for registration failure or a null handle. The
original exception is retained as `__cause__` when one was raised. Failed
initialization and failed registration both attempt to close every prepared
resource, even when another resource's cleanup raises.

`cleanup_errors` counts exceptions during resource closure. `shutdown_timeouts`
counts only a sink writer that did not terminate within the timeout. Background
write failures remain in the sink's `io_errors`; successful shutdown does not
imply successful delivery of every record.

This interface does not prove that a current host implements the proposed
contract. See [`current_host_seam_proposal.md`](current_host_seam_proposal.md)
for the audited gap and candidate host attachment points.

## Closed configuration

Use `ObserverConfig.from_mapping()` for JSON-like input. Unknown keys, aliases,
wrong types, unsafe bounds, incomplete descriptor configuration, and enabled
configuration without an event destination are rejected.

| Key | Default | Constraint |
|---|---:|---|
| `enabled` | `false` | exact Boolean |
| `event_path` | `null` | nonempty path string; required when enabled |
| `descriptor_dir` | `null` | paired with `evidence_label` |
| `evidence_label` | `null` | closed `EvidenceLabel` value |
| `max_pending_records` | `4096` | `1..4096` |
| `max_record_bytes` | `1048576` | `512..1048576` |
| `max_file_bytes` | `67108864` | record bound through `67108864` |
| `max_correlated_transfers` | `4096` | `1..4096` |
| `max_recovery_admissions` | `4096` | `1..4096` |
| `max_descriptor_regions` | `4096` | `1..4096` |
| `max_descriptor_record_bytes` | `1048576` | `1..1048576` |
| `shutdown_timeout_seconds` | `5.0` | finite `0..30` seconds |

Paths are revalidated and opened before host registration, so configuration
that points to a missing directory, symlink destination, or non-regular event
file fails activation. Supplying destinations while `enabled=false` is allowed
for preconfiguration and performs no I/O.

## Current validation level

The filesystem test matrix is Linux/POSIX, using directory file descriptors,
no-follow opens, file locks and POSIX permission bits. Native Windows is not a
validated sink/descriptor platform. WSL validation must use a native Linux
filesystem such as ext4. Host-independent tests can run separately; a skipped
filesystem test is not Windows compatibility evidence.

Fixture and CPU tests cover disabled zero-work behavior, contract and
destination rejection, registration failure cleanup, exact transfer/recovery/
first-compute normalization, optional descriptor support, invalid source data,
callback exceptions, duplicate start/stop, callback races during registration,
unregistration failure, and thread/resource cleanup. Failure-injection tests
also check initialization cause preservation, cleanup after multiple errors,
and the distinction between write failures, close exceptions and timeouts.

The manifest remains `import_only`. A real source-specific binding, accepted
host seam, fixed compatibility matrix, clean enable/disable integration, and
real-hardware evidence are still required before activation.
