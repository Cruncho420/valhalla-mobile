"""Exercise the real shell runner with isolated Gradle/ADB stand-ins, never a device.

The exec-out stand-in preserves argument boundaries, matching the observed device
capture protocol; shell-mode argument joining is a different transport.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / 'run_android_trace_tests.sh'
TARGET = 'com.valhalla.valhalla.test'
DECLARATION = ('instrumentation:com.valhalla.valhalla.test/'
               'androidx.test.runner.AndroidJUnitRunner (target=' + TARGET + ')\n')
REQUIRED = ('VALHALLA_TRACE_AAR', 'VALHALLA_TRACE_AAR_RECEIPT', 'VALHALLA_TRACE_APK_RECEIPT')


class AndroidTraceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='trace-runner-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout with spaces'
        self.root.mkdir()
        (self.root / 'scripts').mkdir()
        shutil.copyfile(RUNNER, self.root / 'scripts/run_android_trace_tests.sh')
        (self.root / 'android').mkdir()
        self.bin = self.root / 'fake bin'
        self.bin.mkdir()
        self.remote = self.root / 'fake Android working directory'
        folder = self.remote / 'files/trace-evidence-owned'
        folder.mkdir(parents=True)
        (folder / 'native.json').write_text('{"fixture":"shell transport only"}\n')
        self.calls = self.root / 'calls.log'
        self.env = os.environ.copy()
        self.env.update({name: 'configured-fixture' for name in REQUIRED})
        # Android tar has no macOS AppleDouble metadata; suppress host-only fixture artifacts.
        self.env['COPYFILE_DISABLE'] = '1'
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FIXTURE_CALLS=str(self.calls), FIXTURE_REMOTE=str(self.remote),
                        FIXTURE_TARGET=TARGET, FIXTURE_INSTRUMENTATION=DECLARATION,
                        FIXTURE_GRADLE_STATUS='0', FIXTURE_CAPTURE_STATUS='0', FIXTURE_PM_STATUS='0')
        self.script(self.root / 'android/gradlew', '''#!/bin/sh
printf 'gradle:%s\\n' "$*" >> "$FIXTURE_CALLS"
printf 'Fixture Gradle output\\n'
exit "$FIXTURE_GRADLE_STATUS"
''')
        self.script(self.bin / 'run-as', '''#!/bin/sh
[ "$1" = "$FIXTURE_TARGET" ] || exit 71
shift
exec "$@"
''')
        self.script(self.bin / 'adb', '''#!/usr/bin/env python3
import os, shlex, subprocess, sys, tarfile
with open(os.environ['FIXTURE_CALLS'], 'a') as log:
    log.write('adb:' + shlex.join(sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['shell', 'pm', 'list', 'instrumentation']:
    sys.stdout.write(os.environ['FIXTURE_INSTRUMENTATION'])
    sys.exit(int(os.environ['FIXTURE_PM_STATUS']))
if sys.argv[1] == 'exec-out':
    if os.environ.get('FIXTURE_EMPTY_CAPTURE') == 'true':
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|'):
            pass
        sys.exit(0)
    if os.environ.get('FIXTURE_CORRUPT_CAPTURE') == 'true':
        sys.stdout.write('not a tar archive')
        sys.exit(0)
    status = int(os.environ['FIXTURE_CAPTURE_STATUS'])
    if status:
        sys.stderr.write('Fixture capture failure\\n')
        sys.exit(status)
    # Actual exec-out capture preserves the sh -c argument; extra quote bytes break it.
    sys.exit(subprocess.run(sys.argv[2:],
                            cwd=os.environ['FIXTURE_REMOTE']).returncode)
sys.exit(72)
''')

    @staticmethod
    def script(path, text):
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, **changes):
        environment = dict(self.env, **changes)
        return subprocess.run(['bash', str(self.root / 'scripts/run_android_trace_tests.sh')],
                              cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)

    def call_log(self):
        return self.calls.read_text() if self.calls.exists() else ''

    def assert_archive(self):
        output = self.root / 'build/test-evidence/android/native-responses.tar'
        with tarfile.open(output, 'r:') as archive:
            names = archive.getnames()
            self.assertIn('files/trace-evidence-owned/native.json', names)
            self.assertEqual(archive.extractfile('files/trace-evidence-owned/native.json').read(),
                             b'{"fixture":"shell transport only"}\n')

    def test_success_captures_valid_tar_with_realistic_remote_quoting(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_archive()
        log = self.call_log()
        self.assertIn(':valhalla:connectedDebugAndroidTest', log)
        self.assertIn('ValhallaRawTraceRouteTest,com.valhalla.valhalla.ValhallaTraceEvidenceTest', log)
        self.assertIn("sh -c 'tar -cf - files/trace-evidence-*'", log)
        self.assertIn('Fixture Gradle output', result.stdout)

    def test_failed_gradle_still_captures_and_preserves_original_status(self):
        result = self.run_script(FIXTURE_GRADLE_STATUS='37')
        self.assertEqual(result.returncode, 37)
        self.assert_archive()
        self.assertIn('adb:exec-out', self.call_log())

    def test_successful_gradle_failed_capture_fails(self):
        result = self.run_script(FIXTURE_CAPTURE_STATUS='23')
        self.assertEqual(result.returncode, 23)
        self.assertIn('Native trace response capture failed', result.stderr)
        capture = self.root / 'build/test-evidence/android/native-capture.log'
        self.assertIn('Fixture capture failure', capture.read_text())

    def test_both_failures_preserve_gradle_status(self):
        result = self.run_script(FIXTURE_GRADLE_STATUS='37', FIXTURE_CAPTURE_STATUS='23')
        self.assertEqual(result.returncode, 37)
        self.assertIn('adb:exec-out', self.call_log())

    def test_missing_instrumentation_target_fails_without_capture(self):
        result = self.run_script(FIXTURE_INSTRUMENTATION='instrumentation:unrelated/Runner (target=other)\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('adb:exec-out', self.call_log())

    def test_ambiguous_instrumentation_target_fails_without_capture(self):
        result = self.run_script(FIXTURE_INSTRUMENTATION=DECLARATION + DECLARATION)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('adb:exec-out', self.call_log())

    def test_invalid_remote_target_is_not_executed(self):
        invalid = DECLARATION.replace(TARGET + ')', 'bad;touch_pwned)')
        result = self.run_script(FIXTURE_INSTRUMENTATION=invalid)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('adb:exec-out', self.call_log())

    def test_missing_required_environment_stops_before_gradle(self):
        for name in REQUIRED:
            with self.subTest(name=name):
                environment = self.env.copy()
                environment.pop(name)
                result = subprocess.run(['bash', str(self.root / 'scripts/run_android_trace_tests.sh')],
                                        env=environment, capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.call_log(), '')

    def test_failed_package_listing_propagates_without_capture(self):
        result = self.run_script(FIXTURE_PM_STATUS='29')
        self.assertEqual(result.returncode, 29)
        self.assertNotIn('adb:exec-out', self.call_log())

    def test_missing_trace_files_cannot_report_capture_success(self):
        shutil.rmtree(self.remote / 'files/trace-evidence-owned')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('adb:exec-out', self.call_log())


    def test_success_exit_with_invalid_archive_does_not_certify_capture(self):
        result = self.run_script(FIXTURE_CORRUPT_CAPTURE='true')
        self.assertNotEqual(result.returncode, 0)


    def test_success_exit_with_empty_archive_does_not_certify_capture(self):
        result = self.run_script(FIXTURE_EMPTY_CAPTURE='true')
        self.assertNotEqual(result.returncode, 0)


    def test_exact_recorded_malformed_request_is_preserved(self):
        request = self.remote / 'files/trace-evidence-owned/malformed-request.json'
        request.write_bytes(b'{')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_archive()
        with tarfile.open(self.root / 'build/test-evidence/android/native-responses.tar') as archive:
            self.assertEqual(archive.extractfile('files/trace-evidence-owned/malformed-request.json').read(), b'{')

    def test_wrong_malformed_request_token_is_rejected(self):
        request = self.remote / 'files/trace-evidence-owned/malformed-request.json'
        request.write_bytes(b'not the recorded malformed input')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)


    def test_gradle_retains_instrumentation_app_for_capture(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('-Pandroid.injected.androidTest.leaveApksInstalledAfterRun=true', self.call_log())


if __name__ == '__main__':
    unittest.main(verbosity=2)
