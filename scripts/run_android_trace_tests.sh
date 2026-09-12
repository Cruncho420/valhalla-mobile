#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${VALHALLA_TRACE_AAR:?Verified trace inputs must be configured}"
: "${VALHALLA_TRACE_AAR_RECEIPT:?AAR receipt must be configured}"
: "${VALHALLA_TRACE_APK_RECEIPT:?APK receipt output must be configured}"
evidence="$PWD/build/test-evidence/android"
mkdir -p "$evidence"
test_status=0
(
    cd android
    ./gradlew :valhalla:connectedDebugAndroidTest --stacktrace \
      -Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true \
      -Pandroid.testInstrumentationRunnerArguments.class=com.valhalla.valhalla.ValhallaRawTraceRouteTest,com.valhalla.valhalla.ValhallaTraceEvidenceTest
) > "$evidence/gradle.log" 2>&1 || test_status=$?
cat "$evidence/gradle.log"

# Capture native responses while the emulator is still alive, including failed tests.
# The target package is obtained from the installed instrumentation declaration.
capture_status=0
if adb shell pm list instrumentation > "$evidence/instrumentation.txt"; then
    target=$(python3 - "$evidence/instrumentation.txt" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
pattern = r'^instrumentation:com\.valhalla\.valhalla\.test/androidx\.test\.runner\.AndroidJUnitRunner \(target=([A-Za-z0-9_.]+)\)\s*$'
targets = re.findall(pattern, text, re.M)
assert len(targets) == 1, 'Expected one installed Valhalla instrumentation target'
print(targets[0])
PY
    ) || capture_status=$?
    if [ "$capture_status" = 0 ]; then
        adb exec-out run-as "$target" sh -c 'tar -cf - files/trace-evidence-*' \
            > "$evidence/native-responses.tar" 2> "$evidence/native-capture.log" || capture_status=$?
    fi
else
    capture_status=$?
fi
if [ "$capture_status" = 0 ]; then
    python3 - "$evidence/native-responses.tar" <<'PY' || capture_status=$?
import json, pathlib, sys, tarfile
archive = pathlib.Path(sys.argv[1])
assert archive.stat().st_size <= 128 * 1024 * 1024, 'Native evidence exceeds capture budget'
count, names = 0, set()
with tarfile.open(archive, 'r|') as stream:
    for index, member in enumerate(stream):
        assert index < 1024, 'Too many native evidence entries'
        parts = pathlib.PurePosixPath(member.name).parts
        assert 2 <= len(parts) <= 3 and parts[0] == 'files' and parts[1].startswith('trace-evidence-')
        assert member.name not in names, 'Duplicated native evidence member'
        names.add(member.name)
        if member.isdir():
            assert len(parts) == 2
            continue
        assert member.isfile() and len(parts) == 3 and parts[2].endswith('.json')
        assert 0 < member.size <= 16 * 1024 * 1024, 'Invalid native JSON evidence size'
        raw = stream.extractfile(member).read()
        if parts[2] == 'malformed-request.json':
            assert raw == b'{', 'Malformed-request fixture differs from its test input'
        else:
            json.loads(raw)
        count += 1
assert count > 0, 'Native response capture is empty'
PY
fi
if [ "$test_status" != 0 ]; then exit "$test_status"; fi
if [ "$capture_status" != 0 ]; then
    echo "Native trace response capture failed" >&2
    exit "$capture_status"
fi
