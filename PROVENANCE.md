# Provenance

This file records ordinary source provenance for the code that may be migrated
into this plugin. Commit IDs identify source history; they are not approval,
freeze, or attestation artifacts.

## Source repositories and licenses

| Source | License | Audited revision |
|---|---|---|
| [vLLM-HUST core legacy](https://github.com/intellistream/vllm-hust-legacy-20260831) | Apache-2.0 | `13f11da44455d2f85f00f810a04e1bd2eaf30ba6` (`main`) |
| [vLLM Ascend HUST legacy](https://github.com/intellistream/vllm-ascend-hust-legacy-20260831) | Apache-2.0 | `3a61053d8b4133eae7c468c1567648048750be21` (`main`) |

The plugin itself is Apache-2.0. Preserve the original commit authorship and
SPDX attribution when code is copied or substantially derived from these
sources.

## Primary histories

| History | State | PR head | Merge commit | PR author |
|---|---|---|---|---|
| [Core #217](https://github.com/intellistream/vllm-hust-legacy-20260831/pull/217) | closed, not merged; replaced by #220 | `7d0c28e436407821315504518009c1cfeb0daa3a` | none | Xie Hanlong (`xiehanlong834-gif`) |
| [Core #220](https://github.com/intellistream/vllm-hust-legacy-20260831/pull/220) | merged | `192aaacab7efa0612c8ac11157dbdfb6ee8372f0` | `9574fc73b0f899b65443cf87ab3d5fb6d368b40a` | Xie Hanlong (`xiehanlong834-gif`) |
| [Core #221](https://github.com/intellistream/vllm-hust-legacy-20260831/pull/221) | closed, not merged; replaced by #236 | `5f7872976bd56a0861bb0072eaac68260ba7d578` | none | Remby Lis (`Remygred`) |
| [Core #236](https://github.com/intellistream/vllm-hust-legacy-20260831/pull/236) | merged | `e6cd22e1a915aedb3a1204cf085fa017557abdf9` | `8d0d044dcd83480036e14c01e161dca525101d93` | Remby Lis (`Remygred`) |
| [Ascend #216](https://github.com/intellistream/vllm-ascend-hust-legacy-20260831/pull/216) | merged | `66ded6084db0ff8fb58fa288dcbfecce54798bdb` | `670c63ebac6b487185a921f17ad0ccff84a740da` | Remby Lis (`Remygred`) |

The source commits have multiple original authors. Core #220 includes work by
Xie Hanlong and Shuhao Zhang (Tony); core #221/#236 and Ascend #216 are by
Remby Lis. Exact author names and email addresses remain in the Git commits and
the preserved patch headers.

## Core commit inventory

The archive contains 25 files, but it does **not** contain 25 independent,
relevant implementation changes.

### Core #217: superseded prototype

- `7d0c28e436407821315504518009c1cfeb0daa3a` — B134 event sink and host-side
  scheduler/CPU/NPU/tiering instrumentation. Author: Xie Hanlong.

### Core #220: seven relevant commits plus one excluded parent patch

- `51fcc999733604c0936f7db6b1c68817ca6f6666` — rebased B134 event sink and
  host insertion points. Author: Xie Hanlong.
- `8662e7edebf5755cb1275f608c20d4283a5fee10` — normalized descriptor-layout
  capture. Author: Shuhao Zhang (Tony).
- `4e2c2044981df8e7f390da46a9d5291056d7d4d8` — reachable CPU-store event and
  restore ordering. Author: Xie Hanlong.
- `744fc865973712cb57260c9f05b956177b5a0650` — runtime-path tests. Author:
  Xie Hanlong.
- `954c9c1471385f78893b9f9757e95a6bab548206` — test-loader isolation. Author:
  Xie Hanlong.
- `da3c6d9218f770aac450b1d6ceb35d5a945b2205` — portable permission test.
  Author: Xie Hanlong.
- `628874305b77ba918d985e00eae07d3408de7922` — SPDX/type/lint fixes. Author:
  Xie Hanlong.
- `192aaacab7efa0612c8ac11157dbdfb6ee8372f0` is a two-parent merge commit. The
  file named `core-pr-220/0008-192aaacab7ef.patch` actually starts with
  `From c5f82a13e863a9b9da26466947e3bf7cfa5a9227` and contains unrelated
  benchmark-publication CI work by `junhuizhang-boop`. It is excluded from KV
  transfer migration. It is retained only so the bootstrap archive remains
  auditable.

### Core #221 and #236: duplicated lineage followed by the merged completion

Core #221 has six commits:

- `0141462f82fb32f78129acccc996212296129eac`
- `837c3585e0c020895af9d450979daabbb4e5f72d`
- `b8d8869e0507dc50b6cf293de58dce202fe2db3e`
- `d5f56f7d678864dc266e7e25c91f08787cc38d15`
- `43509bcf1bd6db470537c1400fce390580f0603b`
- `5f7872976bd56a0861bb0072eaac68260ba7d578`

They introduce the bounded recovery identity spine, scheduler/worker observer
protocols, transfer and wait receipts, invalidation, first-compute observation,
and tests. Core #221 was not merged.

Core #236 repeats those six changes as commits
`f40d540c95ca5be40a72fbb01b2b0a1ced902d62`,
`3e323f6af20bbd2fb1b48687cb290adabb9e4c91`,
`59b345afe1507835f0662f0202d93df5924edade`,
`c689980d199687fbbe97c089c699dcbe96d8c191`,
`ee184ad8b8e8b522d9d7900c427f179a6b2c4ce7`, and
`18add650e20e6b617703add78258fbd7a2f74501`. Stable patch-id comparison
confirms a one-to-one identical mapping in that order. They are duplicate
source representations, not separate behaviors. The merged line then adds:

- `50a6df2bef5c06e7f38107dedd9214e61ff58df1` — complete default-off
  OffloadingConnector sidecars;
- `f6e40cc519b3e185e303cbfb0c9f48ed74b321a2` — avoid disabled-path debug
  formatting;
- `e7f133da1b86887c2d6c0e23dd10f93a2ab689bb` — release transfer attempts that
  cannot be observed;
- `e6cd22e1a915aedb3a1204cf085fa017557abdf9` — pre-commit fixes.

Use the merged #236 lineage as the primary source and #221 only to explain its
pre-merge history.

## Ascend first-compute history

Ascend #216 is the source for the NPU first-compute observation semantics:

- `5c2963256d6fe7e4f13d0c2abbc4cca51b8c66e0` inserts the observation before
  the V1 model forward in `vllm_ascend/worker/model_runner_v1.py`;
- `66ded6084db0ff8fb58fa288dcbfecce54798bdb` moves the optional compatibility
  check into `vllm_ascend/worker/kv_recovery.py` and adds
  `tests/ut/worker/test_kv_recovery_compat.py`.

Both commits are by Remby Lis and are covered by the source repository's
Apache-2.0 license. They are referenced from the source archive rather than
copied into the current core-only patch directory.

## Migration boundary

The detailed file/symbol classification and planned plugin destinations are in
[`docs/source_inventory.md`](docs/source_inventory.md). In summary:

- event schemas, bounded identities, normalization, receipts, safe sinks, and
  correlation state belong in this plugin;
- request scheduling, KV allocation/copy/restore, model forward, connector
  policy, and the minimal callback invocation remain host-owned;
- historical benchmark scripts, assistant-created approval/attestation text,
  and the excluded `c5f82...` patch are not implementation sources.

Closed does not mean merged, and a merged historical change does not prove that
the same seam exists in the current host. Current-host compatibility must be
verified independently before activation.
