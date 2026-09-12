# Synthetic trace evidence

`TestTraceEvidence` uses the real native actor and the existing Andorra graph.
It exercises a route-derived shape, deterministic offsets of roughly 2–3 metres,
out-of-coverage points, a disconnected shape, malformed JSON, and subsequent actor reuse.
Outside/disconnected cases may return structured native errors.
Successful attributes must contain edges, valid shape bounds, and at least two real point-to-edge links.
The exact native `18446744073709551615` index remains unresolved and never counts as a link.
The best-candidate confidence value is not interpreted as a calibrated probability.

Run only this new class from the repository root with an operator-selected simulator:

```sh
VALHALLA_MOBILE_DEV=true \
TEST_RUNNER_VALHALLA_TRACE_EVIDENCE_DIR=/tmp/valhalla-trace-evidence \
xcodebuild test -scheme ValhallaMobile -sdk iphonesimulator \
  -destination 'platform=iOS Simulator,id=<simulator-UDID>' \
  -only-testing:ValhallaTests/TestTraceEvidence \
  -parallel-testing-enabled NO \
  -resultBundlePath <new-result-bundle-path> \
  -skipPackagePluginValidation
```

The optional runner environment variable forwards `VALHALLA_TRACE_EVIDENCE_DIR` to XCTest.
It must name an absolute local directory accessible to the test process.
Each run creates a separate UUID directory, writing at most 32 files of at most 1 MiB each.
Requests and original native responses are retained without reserializing response numeric tokens.
These local files contain synthetic fixture coordinates; the test adds no coordinate console logging.
No files are exported when the variable is absent.
Record the native archive hash, source HEAD and diff, test-source hash, and result bundle separately.

This is bounded fixture evidence, not GPX consumer acceptance, threshold calibration,
source-to-binary provenance, Android parity, or physical-device validation.
Existing trace tests and assertions are unchanged.
