"""Pure identity and dispatch guards; no network, binary execution, or native claims."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import prospective_trace_capture as capture
import bind_prospective_capture as binder
import fetch_prospective_source as fetcher


class ProvenanceTests(unittest.TestCase):
    def test_android_package_binding_and_wrong_or_empty_source_refusal(self):
        revision = 'a' * 40
        source = binder.P.expected_source(revision, binder.P.manifest(ROOT / 'patches/valhalla/manifest.cmake'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / 'raw'
            raw.mkdir()
            package = dict(kind='valhalla-android-package', aar={'sha256': 'b'*64},
                           inputs={abi: {'source': source} for abi in ('arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64')})
            native = dict(kind='valhalla-android-test-apk', aar=package['aar'], apk={'sha256': 'c'*64},
                          native={'x86_64': {'sha256': 'd'*64}})
            receipt = dict(platform='android', complete=True, abi='x86_64', nativeLibrarySha256='d'*64,
                           extractSha256=capture.EXTRACT_SHA, configSha256='e'*64, stageSha256='f'*64)
            (root/'native.json').write_bytes(capture.encoded(native))
            (raw/'receipt.json').write_bytes(capture.encoded(receipt))
            def run():
                binder.bind('android', raw, root/'native.json', root/'package.json', None, root/'proof.json')
            with patch.dict(os.environ, GITHUB_SHA=revision, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1'), \
                    patch.object(binder.subprocess, 'check_output', return_value=revision+'\n'):
                (root/'package.json').write_bytes(capture.encoded(package))
                run()
                proof = json.loads((root/'proof.json').read_text())
                self.assertFalse(proof['productionReadinessAdmitted'])
                self.assertEqual(proof['executed']['librarySha256'], 'd'*64)
                with self.assertRaises(capture.InvalidCapture):
                    run()  # Earlier output is immutable.
                (root/'proof.json').unlink()
                receipt['nativeLibrarySha256'] = '0'*64
                (raw/'receipt.json').write_bytes(capture.encoded(receipt))
                with self.assertRaises(capture.InvalidCapture):
                    run()
                receipt['nativeLibrarySha256'] = 'd'*64
                (raw/'receipt.json').write_bytes(capture.encoded(receipt))
                for inputs in ({}, {'x86_64': {'source': source}}):
                    package['inputs'] = inputs
                    (root/'package.json').write_bytes(capture.encoded(package))
                    with self.assertRaises(capture.InvalidCapture):
                        run()

    def test_wrong_artifact_run_or_digest_refuses_before_download(self):
        good = dict(id=10341602724, expired=False,
                    name='valhalla-andorra-source-evidence-34828738835',
                    workflow_run=dict(id=34828738835, head_sha='8d7a40f450e32efdec0c2c41863b768085593579'),
                    digest='sha256:'+capture.ZIP_SHA, size_in_bytes=8687498)
        import copy
        for key, wrong in [('id', 47303160), ('expired', True), ('digest', 'sha256:'+'0'*64),
                           ('workflow_run', dict(id=34832376641, head_sha='0'*40))]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp:
                value = copy.deepcopy(good)
                value[key] = wrong
                with patch.object(fetcher, 'command', return_value=capture.encoded(value)) as command:
                    with self.assertRaises(capture.InvalidCapture):
                        fetcher.fetch(Path(temp)/'output')
                    self.assertEqual(command.call_count, 1)
                    self.assertFalse((Path(temp)/'output').exists())

    def test_release_guard_executes_before_both_build_roots(self):
        import subprocess
        workflow = (ROOT/'.github/workflows/rods-release.yml').read_text()
        self.assertIn('prospective_capture:', workflow)
        self.assertIn('default: false\n      tag_name:', workflow)
        self.assertIn('  build-ios:\n    needs: admit-mode', workflow)
        self.assertIn('  build-android:\n    needs: admit-mode', workflow)
        self.assertIn('if: ${{ inputs.publish_release && !inputs.prospective_capture }}', workflow)
        start = workflow.index('          if [ "$PROSPECTIVE_CAPTURE"')
        end = workflow.index('\n\n  build-ios:', start)
        script = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
        for prospective, publish, expected in [('true','true',1), ('true','false',0),
                                                 ('false','true',0), ('false','false',0)]:
            result = subprocess.run(['bash','-c',script], capture_output=True,
                                    env=dict(os.environ, PROSPECTIVE_CAPTURE=prospective, PUBLISH_RELEASE=publish))
            self.assertEqual(result.returncode, expected)

    def test_existing_test_classes_and_optional_dispatch_remain_separate(self):
        script = (ROOT/'scripts/run_android_trace_tests.sh').read_text()
        self.assertEqual(script.count('-Pandroid.testInstrumentationRunnerArguments.class='), 1)
        self.assertIn('class=com.valhalla.valhalla.ValhallaRawTraceRouteTest,com.valhalla.valhalla.ValhallaTraceEvidenceTest\n', script)
        self.assertLess(script.index('if [ "$test_status" != 0 ]'), script.index('bash scripts/run_android_prospective_capture.sh'))
        self.assertIn('if [ "${VALHALLA_PROSPECTIVE_INPUT:-}" != "" ]; then', script)


if __name__ == '__main__':
    unittest.main()
