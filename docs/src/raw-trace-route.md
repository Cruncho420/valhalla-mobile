# Raw trace route

`ValhallaRaw.traceRoute(request: String)` on Android and
`Valhalla.traceRoute(rawRequest: String)` on Apple call `actor_t::trace_route`.
The request must contain a Valhalla trace shape or encoded polyline and costing.
Setting an `action` property on a `route` request does not select map matching.
No route fallback is used by these new entry points.

Both operations reuse the existing persistent actor.
Android synchronizes route, trace route, and close on the same instance;
close remains idempotent, and either operation after close throws `IllegalStateException`.
If actor creation fails, trace route returns error JSON and does not create a temporary routing actor.
Apple serializes both operations with the same Objective-C lock and destroys its actor on deallocation.
The new Apple bridge is a C++ `std::string trace_route(const char*, void*)` function,
mirroring the existing route function; this is not an independently exported C ABI.
Rods already links the existing C++ `route(const char*, void*)` through its Objective-C++ wrapper
and exception guard, so this extension needs the analogous `trace_route` linkage and rebuilt library;
no separate C ABI is required.

Engine failures return `{"code": ..., "message": ...}` JSON with escaped messages.
JNI releases its acquired request string even when trace evaluation or error serialization fails.
Objective-C++ and JNI catch C++ failures at the platform boundary.
No shape coordinates or raw request bodies are logged by the new action.

## Verification

`ValhallaRawTraceRouteTest` and `TestTraceRoute` use the existing Andorra tiles.
They obtain a real route polyline, submit it without locations, check that route rejects it,
and check that trace route returns a nonempty route shape.
They also exercise malformed JSON, a subsequent successful trace, and ordinary routing on the same actor.
Android additionally checks failed actor creation, repeated close, and both operations after close.
Run them with the existing connected Android test and Xcode simulator test workflows,
after rebuilding native binaries from this source.
A published older binary cannot supply the new native symbol.

## Source provenance

At the start of this change, all three `rods-0.5.1-k21*` tags resolved to
`1692c2afca0642da57dac0576bad998d846c9784`, which did not contain `ValhallaRaw`.
The persistent actor and deterministic close source came from
`origin/rods/0.5.1-kotlin21` at `6a7c0bf79761ef6741b332dc5af867561f83deea`.
This extension starts directly from that exact r3 source commit, retaining its build toolchain.
An initial mixed upstream merge was preserved separately and is not the extension base.
These observations do not establish the source provenance of any existing bundled release asset.
New artifacts require recorded source commit, hashes, and device API smoke tests before pinning.

## Build-only CI validation

The manual `rods-release.yml` workflow accepts a boolean `publish_release` input.
It defaults to `true`, preserving the existing release workflow behavior.
Set it explicitly to `false` to run every existing iOS and Android build and packaging job
while skipping the release job, which is the only job with write permission.
No release or tag is created in this mode.
The AAR and XCFramework remain available as workflow artifacts;
release checksum companion files are generated only by the skipped release job.

After the reviewed branch is available remotely, dispatch build-only validation with:

```sh
gh workflow run rods-release.yml \
  --repo Cruncho420/valhalla-mobile \
  --ref codex/valhalla-trace-r3 \
  -f publish_release=false \
  -f tag_name=trace-route-ci
```

`tag_name` is still required because it also names the AAR output;
with publishing disabled, it is an artifact filename label and does not create a tag.
Record the resulting run URL, checked-out source SHA, and downloaded artifact hashes.
A successful build checks compilation and packaging, not device map matching or lifecycle behavior;
the platform tests above and Rods integration tests still need execution against the rebuilt artifacts.
This document describes the dispatch procedure and does not record a completed CI run.

## Native match evidence

`ValhallaRaw.traceAttributes(request: String)` and `Valhalla.traceAttributes(rawRequest: String)`
call `actor_t::trace_attributes` on the same actor, with the same serial execution and error barriers.
Apple links `trace_attributes(const char*, void*)`; Android uses `nativeTraceAttributes(long, String)`.
Use `shape_match: "map_snap"` and native JSON output for match evidence.
The engine's `edge_walk` path supplies a synthetic score tuple and no matched point results.
The wrapper returns the native response unchanged; it does not calculate an acceptance score.

In the pinned engine, trace-route OSRM confidence is hardcoded to 1.
Trace-attributes `confidence_score` is also 1 for the best candidate;
alternatives receive the best raw score divided by their raw score.
Neither value is a calibrated probability or a replacement for a Mapbox confidence threshold.
`raw_score` is the native matcher score, and `matched_points` describes individual input samples.
Matching evidence includes `type` (`matched`, `interpolated`, or `unmatched`), `edge_index`,
`distance_from_trace_point`, and optional route-discontinuity flags.
Distance fields are omitted for unmatched points, and discontinuity flags are emitted only when true.
Consumers must request the needed filters and assess evidence before deciding to accept a GPX trace.

Source references at the unchanged `e2f017b16080f49203de245a211b09efab09cf72` engine pin:
[score construction](https://github.com/valhalla/valhalla/blob/e2f017b16080f49203de245a211b09efab09cf72/src/thor/trace_route_action.cc#L240),
[matched-point serialization](https://github.com/valhalla/valhalla/blob/e2f017b16080f49203de245a211b09efab09cf72/src/tyr/trace_serializer.cc#L434),
and [trace-attributes dispatch](https://github.com/valhalla/valhalla/blob/e2f017b16080f49203de245a211b09efab09cf72/src/thor/trace_attributes_action.cc#L41).

The platform tests explicitly request score and matched-point filters, require multiple matched samples,
finite native scores, edge indices, nonnegative finite distances, and recovery after malformed JSON.
These new entry points and assertions postdate source `2cec28680e4c4b0b0667a606e4e4c9e1644b762e`;
CI run `34267116238` building that earlier source cannot validate this extension.
New native builds, symbol checks, and platform test runs remain required.
