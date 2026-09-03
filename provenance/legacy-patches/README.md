# Legacy patch archive

This directory preserves the bootstrap exports associated with core PRs #217,
#220, #221, and #236. Author metadata, messages, trailers, and exact diffs are
retained for audit; files are not automatically eligible migration sources.

`core-pr-220/0008-192aaacab7ef.patch` is a known extraction-boundary error. Its
file name refers to merge commit `192aaacab7efa0612c8ac11157dbdfb6ee8372f0`,
but its patch header and content are for unrelated benchmark-publication parent
`c5f82a13e863a9b9da26466947e3bf7cfa5a9227`. It is explicitly excluded from KV
transfer migration and retained only to make the bootstrap error visible.

The first six #221 and first six #236 patches have matching stable patch IDs;
they are two histories of the same changes, not twelve independent changes.
The merged #236 lineage is the primary source.

Ascend PR #216 is recorded by exact commit and file in `PROVENANCE.md`. Its
patches are not currently duplicated into this core-only archive.

Event and observer modules should become extension-owned; scheduler and model
runner insertion points require versioned host contracts.

Historical approval, attestation, or content-freeze prose found in a patch is
non-normative and must not be replayed as a current project requirement.
