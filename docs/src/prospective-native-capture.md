# Prospective native capture preparation

This is opt-in test tooling, not release admission or matcher acceptance.
It executes only independently reviewed public-source request bytes.
Native returns cannot create road labels, and no collector creates a consumer/stitch receipt.
Raw UTF-8 bytes mean the exact request supplied to, and string returned by, the platform wrapper;
they are not a claim about historical production network serialization.

## Frozen single-window authority

`test-fixtures/prospective-v1/manifest.json` is copied without reserialization from the reviewed
`prospective-request-stage-v1/requests` artifact.
Its file SHA-256 is `0729b13a8334f964d72d57ac275c4f4082e8ec5f21cb2b41c6a401d6f9e59abc`.
Its request contract is `1d1ef0680d7407f4b6cd9f8a2df8b9819a0d9b026c1a596705700d0c4391feab`;
its independent source oracle is `de25d03fbc65e4b119fbf051881356bd10de2ede98ece90b5eb369347b28b669`.
The enclosing reviewed request-stage contract is
`0cdb9ffb5c45309ff6e1b25fba6675de6cef2b05002e20db6aadce4379acba60`.
The manifest fixes all 55 public fixtures, original/resampled variants, split, window bounds,
and exact original/diagnostic request sizes and hashes.
The 110 diagnostic requests execute once in manifest order;
all 110 original requests are retained as source evidence, without additional native calls.
Only the diagnostic attribute additions already frozen by that source contract are used.
An extra request, altered byte, missing file, or alternative manifest is refused before execution.
The original group remains independently pinned; the combined run does not replace its manifest.

## Reviewed additive group

`test-fixtures/prospective-composite-v1/manifest.json` retains the separately reviewed 33 windows:
24 long-route/short-tail companions and 9 dense source windows.
Its exact file SHA-256 is `cba47d133c2d6ff62d95139baca0ed5c5d5295971f9ac486325a10b6aeb6347b`;
its request contract is `b487eebba7e3743b5909bdfd8edc538b78592ff3b351aff9fe416f2a3d83e477`.
Its enclosing request-stage contract is
`110a7465be2e484d15b6d90f941d24a49b00beda9246c7c288407810a35ae279`;
the underlying composite source protocol v2 is
`f6288c3b2e10d7baed4aaf7ce72cdf1c30e09ff1cc7d5fbf651eb84325526894`.
The exact manifest retains the additional source authority pins and all 66 request-file identities.
Every `windowIndex`, global window boundary, `sourceInputIndices`, and `originalSourceIndices`
is copied without flattening, renumbering, or calculating labels from native responses.
The 193-point original tail retains indices 184 through 192, including eight shared samples
and one new sample; its resampled companion remains a separate frozen sequence.

Before the whole-trip additive group, the stage records two ordered attribute manifest groups and 143 calls:
the original 110, then these 33.
A row has a separate global capture `index`; that index never replaces source or window indices.
Both collectors now require the full combined inventory, and the host re-derives exact identities from
all pinned manifests before admitting a capture.
An old 110-only receipt, missing/duplicated window, self-consistently renumbered tail, or unreviewed
third group is refused.
This expands capture preparation only; consumer preservation and multiwindow product admission
remain separate and unproven.

## Frozen whole-trip route authority

`test-fixtures/prospective-trip-v1/manifest.json` is a third, additive input group containing
118 separately frozen `trace_route` requests. Its exact manifest SHA-256 is
`48aab217f31334efa69b524f0d405ee3b105d70e984c62eefbbc0c2b1d590074` and its production
`JSON.stringify` request-contract SHA-256 is
`e2dfeb8214116e54b9413f69a43fa3b60bc3f0820d1e58a71cc027c2bf5e8eff`.
Each request is admitted only with its original action, shape, costing, and shape-match bytes;
the host refuses changed bytes, a changed action, a missing/duplicate/reordered row, or an extra file.
The staged row preserves the source group separately from its global capture index, and the collectors
dispatch `trace_route` only for this group rather than translating it into `trace_attributes`.

Before the importer group below, the combined cohort contained the original 143 `trace_attributes`
calls followed by these 118 whole-trip `trace_route` calls. That earlier cohort was source preparation
and raw-native-return evidence only; it did not accept map-matching outcomes or establish consumer,
user-device, or production behavior.

## Frozen importer attribute authority

`test-fixtures/prospective-importer-v1/manifest.json` is a fourth, additive input group containing
the 22 `trace_attributes` windows which the original source-only importer inventory found were not
represented by either earlier attributes group. Its exact manifest SHA-256 is
`4c77cc83d895188a80c74c1554908f497916bee49c352d79b520c62627191877`, and its production
`JSON.stringify` request-contract SHA-256 is
`a56e32a5fa31dcbfa8e45fb88075742afda942c3b0c3c14c0593347941b4af10`.
Every row retains the original fixture, split, resampled recording length, selected-window bounds,
and strictly increasing original source indices. The request bytes come from the immutable importer
inventory SHA-256 `9ce80de6477a62eb80a2a95a33c5a01a3b1a7ee2ef93873704916ef5156a9a97` and its preparation
SHA-256 `c25f55abd1e3b07a7d1a5bbb5f6b69b8145db8bd9a5c669720f9787ef9826ebd`; they are copied, not
reserialized or reconstructed. The absence of an `original` request file is deliberate: these are
the exact post-resampling importer calls, not a synthetic paired input.

The combined capture is now exactly 283 calls: 165 `trace_attributes` calls followed by 118
whole-trip `trace_route` calls. Both native collectors choose the actual `trace_attributes` wrapper
operation for this group while retaining the one-request schema; missing, repeated, reordered,
byte-changed, action-changed, or source-index-changed inputs are refused by the host before native
execution. This extends source preparation only. It neither admits importer outcomes nor establishes
consumer, user-device, or production behavior.

## Exact source graph

The graph is not the existing bundled native-test graph and is not rebuilt.
GitHub artifact **10341602724** belongs to source run **34828738835** at source workflow revision
`8d7a40f450e32efdec0c2c41863b768085593579`.
**47303160 is an attestation ID, not an artifact ID.**
The source ZIP digest is `4f15fea95e686b0924fc4e14a113f09101ea3ef562ee34682b6da34c771af6ea`;
its compressed graph digest is `d69510b46c5d1d2663ea0ac095039b80406ad51edb26db016401e7b80aa18c6c`.
The decompressed extract is exactly 3,051,520 bytes with digest
`c0957c92bb71833ed3763e4b2c42a536cb28f2bcb69c991264532485edee75d4`.
This is the graph independently enumerated by census run 34832376641, not an inference from a polygon.
The fetcher checks live artifact/run/digest identity, the explicit attestation bundle ID,
and `gh attestation verify` against both the pinned source and signer revisions.
A missing or expired retained artifact stops the workflow; there is no latest-graph fallback.
No graph or downloaded binary is checked into this repository.

## Isolated execution and receipts

`prospective_capture` defaults false.
Combining it with `publish_release=true` fails in a prerequisite before either native build root;
the release job also independently excludes prospective capture.
Existing six-method native behavior, graphs, and result requirements are unchanged.
Android first completes that verified instrumentation run, then copies the admitted stage into
an absent test-owned private directory and runs only the new collector.
iOS runs its collector in a separate XCTest invocation with the same run's verified local framework.
The actor receives a separate config whose only template change is the exact staged graph path.
Every capture records the original fixture/variant/split/window identity, row order, request and
response hashes/sizes, stage hash, graph hash, actual config hash, and producing ABI.
The raw response is retained whole, including native refusals and every alternate;
no response field is used as an expected road label.
A bridge exception, missing row, altered byte, unsupported ABI, or oversize return is incomplete,
never a partial success or a response truncated to fit the limit.

Android reads and hashes the actual loaded library and binds it to the verified test APK/AAR chain.
iOS records the actual executing XCTest bundle's hash and ABI, then the host compares that
bundle on disk and records the same-run package/source authority.
Host provenance includes workflow run/attempt, wrapper/core/patch source identity,
collector source hash, executed binary identity, and capture/config/graph/stage hashes.
These are trusted-workflow receipts, not independent signatures against a malicious producer.
They do not establish user-device acceptance or faithful production consumer behavior.

The current runtime targets are Android x86_64 and the actual selected iOS Simulator ABI.
Building other ABI slices does not mean those slices executed this cohort.
Physical iPhone/Android arm64, 32-bit behavior, consumer preservation, and product acceptance
require separate reviewed evidence.

## Bounds and failure publication

Each raw response is limited to 1 MiB, every run to the exact 283 planned calls,
and raw artifact publication to 128 MiB.
The collectors publish a completion receipt only after every return is saved.
The host verifies exact file inventory and bytes, not just successful XCTest/Gradle status.
Inputs, native build logs, result bundles, and transfer archives stay outside the upload root.
Only bounded raw files and compact verification/provenance/publication receipts are uploaded;
invalid or oversized publication emits a small `INCOMPLETE` receipt.
Partial bounded raw returns remain useful failure evidence, and are explicitly incomplete.
Old capture/input directories and published destinations are never overwritten.

## Host checks and later execution

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p 'test_prospective*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p test_android_trace_runner.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/tests -p test_native_test_results.py -v
```

These checks use synthetic responses and never execute the native matcher.
Swift parsing and workflow YAML parsing are preparation checks, not native compilation.
After independent review and explicit CI authorization, artifact-only dispatch uses
`prospective_capture=true,publish_release=false` on the exact reviewed revision.
No dispatch, native outcomes, consumer receipt, or production release is authorized by this document.
