#!/bin/bash
# Explicit optional follow-on after the unchanged six-method native trace suite.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${VALHALLA_PROSPECTIVE_INPUT:?Reviewed stage is required}"
: "${VALHALLA_PROSPECTIVE_STAGE_SHA256:?Stage identity is required}"
: "${VALHALLA_PROSPECTIVE_TARGET:?Verified instrumentation target is required}"
[[ "$VALHALLA_PROSPECTIVE_TARGET" =~ ^[A-Za-z0-9_.]+$ ]]
[[ "$VALHALLA_PROSPECTIVE_STAGE_SHA256" =~ ^[a-f0-9]{64}$ ]]
evidence="$PWD/build/prospective-private/android"
mkdir -p "$evidence"
tar -cf "$evidence/input.tar" -C "$VALHALLA_PROSPECTIVE_INPUT" .
adb exec-in run-as "$VALHALLA_PROSPECTIVE_TARGET" sh -c \
  'mkdir files/prospective-input && cd files/prospective-input && tar -xf -' < "$evidence/input.tar"
status=0
adb shell am instrument -w -r \
  -e class com.valhalla.valhalla.ValhallaProspectiveTraceCaptureTest \
  -e prospectiveStageSha256 "$VALHALLA_PROSPECTIVE_STAGE_SHA256" \
  com.valhalla.valhalla.test/androidx.test.runner.AndroidJUnitRunner \
  > "$evidence/instrumentation.txt" 2>&1 || status=$?
# Keep the exact raw refusals and alternates even if the method failed part way through.
capture_status=0
(ulimit -f 131072; adb exec-out run-as "$VALHALLA_PROSPECTIVE_TARGET" sh -c \
  'tar -cf - files/prospective-capture' > "$evidence/capture.tar") || capture_status=$?
if [ "$capture_status" = 0 ]; then
  python3 scripts/prospective_trace_capture.py unpack --archive "$evidence/capture.tar" \
    --output "$evidence/raw" || capture_status=$?
fi
# The instrumentation transcript and the collector directory listing are the only evidence of
# why a capture produced nothing, and the private evidence directory is never uploaded.
diagnostics="$PWD/build/test-evidence/android"
mkdir -p "$diagnostics"
head -c 65536 "$evidence/instrumentation.txt" > "$diagnostics/prospective-instrumentation.txt" || true
adb exec-out run-as "$VALHALLA_PROSPECTIVE_TARGET" sh -c 'ls -l files files/prospective-capture' \
  2>&1 | head -c 65536 > "$diagnostics/prospective-files-listing.txt" || true
if [ "$status" != 0 ]; then exit "$status"; fi
if [ "$capture_status" != 0 ]; then exit "$capture_status"; fi
python3 - "$evidence/instrumentation.txt" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
assert len(re.findall(r'^OK \(1 test\)\s*$', text, re.M)) == 1, 'Prospective instrumentation method did not pass exactly once'
assert not any(value in text for value in ('FAILURES', 'INSTRUMENTATION_FAILED', 'shortMsg='))
PY
python3 scripts/prospective_trace_capture.py verify --stage "$VALHALLA_PROSPECTIVE_INPUT" \
  --capture "$evidence/raw" --platform android > "$evidence/capture-verification.json"
python3 scripts/bind_prospective_capture.py --platform android --capture "$evidence/raw" \
  --native-receipt "$VALHALLA_TRACE_APK_RECEIPT" --package-receipt "$VALHALLA_TRACE_AAR_RECEIPT" --output "$evidence/provenance.json"
