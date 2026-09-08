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
