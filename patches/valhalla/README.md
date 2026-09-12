# Reviewed downstream Valhalla patch

This directory records a downstream fix on Valhalla 3.6.3 at the exact core commit in
`manifest.cmake`.
The upstream gitlink remains unchanged; the compiled core is **patched**, not pristine upstream.

`0001-meili-stateful-terminal-segment.patch` fixes a stateful trace boundary whose single matched
edge describes the outgoing direction while its incoming transition ends on the opposing edge.
The terminal state belongs to the routed transition's last segment.
Interior point searches remain strict, and neither direction nor an alternative is discarded.

`src/CMakeLists.txt` includes `cmake/PrepareValhalla.cmake` before configuring the core.
The helper requires an initialized core with the exact HEAD and committed/staged parent gitlink,
verifies patch and source SHA-256 values, and applies the patch only to exact original bytes.
An exact already-patched source is accepted without rewriting it.
A shared Git-directory lock serializes concurrent architecture preparation.
Unexpected tracked edits, staged edits, nonignored untracked files, file-mode changes,
and failed patch operations stop the build;
the helper never resets or overwrites unexpected work.
Both platform scripts stop when configuration fails, preventing a stale build from continuing.

The four `set(name "hex")` entries in `manifest.cmake` are also a strict data contract for artifact
provenance tooling: `expected_core`, `original_sha`, `patched_sha`, and `patch_sha`.
Do not change these values to accept an unexplained source difference.
A future upstream upgrade must explicitly re-review the fix and either replace or remove it once
an equivalent upstream fix passes the same regressions.

Run the isolated gate tests from the wrapper root:

```sh
python3 scripts/tests/test_core_patch_gate.py
```

The tests use real temporary Git repositories and the actual CMake helper.
Only the expected core commit is rebound to the synthetic repository commit; original fixture bytes,
patch bytes, source hashes, and patch application remain genuine.
Git failure injection covers both the preliminary check and the subsequent application.
Configure/build command stand-ins verify failure propagation without compiling native dependencies.
The source fixture is the unchanged pinned `src/meili/match_route.cc` and carries its SHA in the
manifest.

These gate tests do not replace native regression tests or a full iOS and Android rebuild.
Artifact packaging must independently validate source and binary provenance when it consumes
prebuilt libraries without running CMake.
No release, product dependency update, confidence acceptance, or device validation is implied by
successful patch preparation.
