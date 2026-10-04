# Reviewed downstream Valhalla patch series

This directory records downstream fixes on Valhalla 3.6.3 at the exact core commit in
`manifest.cmake`.
The upstream gitlink remains unchanged; the compiled core is **patched**, not pristine upstream.
The patches form an ordered series: each one applies on top of the ones before it, in the order
`manifest.cmake` lists them.

## 0001-meili-stateful-terminal-segment.patch

Fixes a stateful trace boundary whose single matched edge describes the outgoing direction while
its incoming transition ends on the opposing edge.
The terminal state belongs to the routed transition's last segment.
Interior point searches remain strict, and neither direction nor an alternative is discarded.
Changes `src/meili/match_route.cc`.

## 0002-meili-bounded-topk.patch

Bounds meili's top-k (alternates) map-matching search, which on dense road networks can skip
thousands of near-identical duplicate paths, each one enlarging the search graph.

- `meili.default.alternates_cost_band`: paths come cheapest-first, so the search stops once the
  next path costs more than the best path plus the band (compared as the float scores a caller is
  shown). Negative (the default) never stops early.
- `meili.default.alternates_max_iterations`: after that many search rounds the request is refused
  with the new error 447 "Map Match alternates search exceeded its bound". `trace_attributes`
  passes 447 through; every other map-match failure there is still 444. 0 (the default) is
  unbounded.
- Every search round polls the caller's interrupt, so a server request timeout stops the search.
  This poll is not configuration-gated; the two bounds are inert unless configured.

A request that asks for no alternates runs exactly one round and is unaffected by all three.
Changes `src/exceptions.cc`, `src/meili/config.cc`, `src/meili/map_matcher.cc`,
`src/thor/trace_attributes_action.cc`, and `valhalla/meili/config.h`.

## Gate

`src/CMakeLists.txt` includes `cmake/PrepareValhalla.cmake` before configuring the core.
The helper requires an initialized core with the exact HEAD and committed/staged parent gitlink
and verifies every patch's SHA-256.
If every listed core source has its original SHA-256, it concatenates the hash-checked patch bytes
into one input inside the core's Git directory, runs `git apply --check` and then `git apply` on
that single input (all of it or nothing; separate patch-file arguments would be written one by
one), and requires every listed source to reach its patched SHA-256 with no other change; if the
applied result is anything else (a wrong manifest hash, a file the manifest does not list), the series is
reversed with `git apply -R` before the build fails, so the core is left exactly as it was found.
An exactly patched tree is accepted without rewriting it.
Any other combination, including a partially patched tree, is a checksum mismatch and is never
rewritten.
A shared Git-directory lock serializes concurrent architecture preparation.
A core prepared by an earlier series (for example r4's `0001` alone) is a partial tree to this gate and is
refused, not upgraded: restore the listed sources first (`git -C src/valhalla checkout -- <the six listed files>`),
then configure again.
Tracked edits outside the listed sources, staged edits, nonignored untracked files,
file-mode changes, and failed patch operations stop the build;
the helper never resets or overwrites unexpected work.
Both platform scripts stop when configuration fails, preventing a stale build from continuing.

## Manifest data contract

`manifest.cmake` is included by CMake and also parsed line by line as strict data by
`scripts/native_artifact_provenance.py`. Besides blank and `#` comment lines it holds exactly:

- one `set(expected_core "<40 hex>")`;
- one or more `list(APPEND core_patches "<file name>" "<sha256 of the patch bytes>")`, in
  application order (file names are relative to this directory);
- one or more `list(APPEND core_sources "<core-relative path>" "<sha256 before the series>"
  "<sha256 after it>")`, one per core file the series changes.

Unknown lines, duplicates, empty lists, non-lowercase or wrong-length hex, and absolute or `..`
paths are rejected.

Build receipts keep their source-identity keys; for a series they are defined as:

- `patchSha256`: SHA-256 of each patch's SHA-256 hex value followed by `\n`, in series order
  (`printf '%s\n' <sha1> <sha2> | shasum -a 256`);
- `originalSourceSha256`: SHA-256 of the lines `<path> <original sha256>\n` over the listed sources
  in manifest order;
- `patchedSourceSha256`: the same over `<path> <patched sha256>\n`.

Do not change these values to accept an unexplained source difference.
A new patch is appended to the series with its own hash, and every core file it touches is listed
(or, if already listed, has its patched hash updated).
A future upstream upgrade must explicitly re-review every patch and either replace or remove it
once an equivalent upstream fix passes the same regressions.

## Tests

Run the isolated gate tests from the wrapper root:

```sh
python3 scripts/tests/test_core_patch_gate.py
```

The tests use real temporary Git repositories and the actual CMake helper.
Only the expected core commit is rebound to the synthetic repository commit; original fixture bytes,
patch bytes, source hashes, and patch application remain genuine.
`scripts/tests/fixtures/core/` mirrors the pinned core paths with the unchanged original bytes of
every listed source, so the real series is applied and every patched hash is asserted.
Git failure injection covers the preliminary check, the subsequent application, and a series whose
second patch cannot apply.
Configure/build command stand-ins verify failure propagation without compiling native dependencies.

These gate tests do not replace native regression tests or a full iOS and Android rebuild.
Artifact packaging must independently validate source and binary provenance when it consumes
prebuilt libraries without running CMake.
No release, product dependency update, confidence acceptance, or device validation is implied by
successful patch preparation.
