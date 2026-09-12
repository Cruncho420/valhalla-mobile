"""PURPOSE: Verify build/provenance ordering with real Git and tiny mock native outputs.
RESPONSIBILITY: Exercise both production shell scripts without a native toolchain build.
DEPENDENCIES: Existing isolated provenance fixture, unittest, and standard library.
CONSUMERS: Build script regression checks.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_native_artifact_provenance as fixtures

P = fixtures.P

ROOT = Path(__file__).resolve().parents[2]


class BuildProvenanceTests(unittest.TestCase):
    git = fixtures.ProvenanceTests.git
    init = fixtures.ProvenanceTests.init

    def setUp(self):
        fixtures.ProvenanceTests.setUp(self)
        moved = self.root / "repo with spaces"
        self.repo.rename(moved)
        self.repo = moved
        self.core = moved / P.CORE_PATH
        self.manifest = moved / "patches/valhalla/manifest.cmake"
        (moved / "scripts").mkdir()
        for name in ("build_apple.sh", "build_android.sh", "native_artifact_provenance.py"):
            shutil.copyfile(ROOT / "scripts" / name, moved / "scripts" / name)
        (moved / ".gitignore").write_text("/build/\n")
        self.git(moved, "add", ".")
        self.git(moved, "commit", "-qm", "Build fixture")
        self.bin = self.root / "mock bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.write_tool("cmake", self.cmake_program())
        self.write_tool("xcrun", self.xcrun_program())
        self.write_tool("xcodebuild", "pass\n")
        self.write_tool("nproc", "print(2)\n")
        self.write_tool("sysctl", "print(2)\n")
        self.ndkCompiler = self.root / "ndk tools/toolchains/llvm/prebuilt/test-host/bin/clang++"
        self.ndkCompiler.parent.mkdir(parents=True)
        self.ndkCompiler.symlink_to(self.compiler)

    def elf(self, *args, **kwargs):
        return fixtures.ProvenanceTests.elf(self, *args, **kwargs)

    def write_tool(self, name, program):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + program)
        path.chmod(0o755)

    def cmake_program(self):
        return '''import json, os, pathlib, struct, sys
p=pathlib.Path.cwd(); env=os.environ
build=sys.argv[1]=='--build'
with open(env['CALL_LOG'],'a') as f: f.write(json.dumps({'event':'build' if build else 'configure',
 'sourcePresent':(p/'native-source.json').exists(), 'arguments':sys.argv[1:]})+'\\n')
if not build:
 if env.get('FAIL_CONFIG'): sys.exit(37)
 cache='other:STRING=missing\\n' if env.get('BAD_CACHE') else 'CMAKE_CXX_COMPILER:FILEPATH='+env['COMPILER']+'\\n'
 (p/'CMakeCache.txt').write_text(cache)
else:
 if env.get('FAIL_BUILD'): sys.exit(42)
 if env.get('MUTATE_SOURCE'): (pathlib.Path(env['REPO'])/'tracked.txt').write_text('during build')
 if '--target' in sys.argv:
  artifact=p/'install/lib/libengine.a'; data=b'owned tiny archive fixture'
  header=p/'install/include/wrapper.h';header.parent.mkdir(parents=True,exist_ok=True);header.write_text('native header')
 else:
  artifact=p/'wrapper/libvalhalla-wrapper.so'; data=bytearray(64)
  data[:7]=b'\\x7fELF'+bytes([2,1,1]);struct.pack_into('<HH',data,16,3,183)
 artifact.parent.mkdir(parents=True,exist_ok=True);artifact.write_bytes(data)
 if env.get('STALE'): os.utime(artifact,ns=(1,1))
'''

    def xcrun_program(self):
        return '''import json,os,pathlib,sys
if sys.argv[1]=='libtool':
 with open(os.environ['CALL_LOG'],'a') as f:f.write(json.dumps({'event':'aggregate'})+'\\n')
 if os.environ.get('FAIL_AGGREGATE'):sys.exit(44)
 pathlib.Path(sys.argv[sys.argv.index('-o')+1]).write_bytes(b'owned aggregate fixture')
elif sys.argv[1:]==['--sdk','iphonesimulator','--find','clang++']:print(os.environ['COMPILER'])
elif sys.argv[1]=='lipo':print('arm64')
elif sys.argv[1]=='otool':print(' platform 7')
else:sys.exit(45)
'''

    def run_build(self, apple=False, **flags):
        env = {**os.environ, "CI": "false", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "VCPKG_ROOT": str(self.root / "vcpkg tools"), "ANDROID_NDK_HOME": str(self.root / "ndk tools"),
               "COMPILER": str(self.compiler), "REPO": str(self.repo), "CALL_LOG": str(self.log), **flags}
        script, abi = ("build_apple.sh", "arm64-ios-simulator") if apple else ("build_android.sh", "arm64-v8a")
        return subprocess.run(["bash", str(self.repo / "scripts" / script), abi], cwd=self.repo,
                              env=env, capture_output=True, text=True, timeout=20)

    def artifact(self, apple=False):
        if apple:
            return self.repo / "build/apple/arm64-ios-simulator/libvalhalla_all.a"
        return self.repo / "build/android/arm64-v8a/wrapper/wrapper/libvalhalla-wrapper.so"

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_both_builds_capture_before_build_and_emit_after(self):
        for apple in (False, True):
            with self.subTest(apple=apple):
                result = self.run_build(apple, CI="true")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                artifact = self.artifact(apple)
                receipt = json.loads(Path(str(artifact) + ".provenance.json").read_text())
                self.assertTrue(receipt["source"]["clean"])
                self.assertTrue(receipt["toolchain"]["id"].startswith("cmake-cache-sha256:"))
                self.assertEqual(receipt["toolchain"]["compilerSha256"], P.file_hash(self.compiler))
        self.assertTrue(all(e["sourcePresent"] for e in self.events() if e["event"] == "build"))
        self.assertEqual([e["event"] for e in self.events()],
                         ["configure", "build", "configure", "build", "aggregate"])

    def test_apple_build_receipt_binds_installed_headers(self):
        result = self.run_build(True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = self.artifact(True)
        receipt = P.load_json(Path(str(artifact) + ".provenance.json"))
        headers = artifact.parent / "install/include"
        self.assertEqual(receipt["headers"], P.tree_identity(headers))

    def test_apple_records_selected_compiler_explicitly(self):
        result = self.run_build(True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        configure = next(e for e in self.events() if e['event'] == 'configure')
        self.assertIn('-DCMAKE_CXX_COMPILER:FILEPATH=' + str(self.compiler),
                      configure['arguments'])

    def test_android_records_compiler_and_release_configuration(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        configure = next(e for e in self.events() if e['event'] == 'configure')
        self.assertIn('-DCMAKE_CXX_COMPILER:FILEPATH=' + str(self.ndkCompiler), configure['arguments'])
        self.assertIn('-DCMAKE_BUILD_TYPE=Release', configure['arguments'])

    def test_android_missing_compiler_fails_before_configuration(self):
        self.ndkCompiler.unlink()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events(), [])

    def test_failed_native_build_does_not_emit(self):
        for apple in (False, True):
            result = self.run_build(apple, FAIL_BUILD="1")
            self.assertEqual(result.returncode, 42)
            self.assertFalse(Path(str(self.artifact(apple)) + ".provenance.json").exists())
        self.assertNotIn("aggregate", [e["event"] for e in self.events()])

    def test_successful_configuration_cannot_attest_stale_unreceipted_output(self):
        for apple in (False, True):
            result = self.run_build(apple, STALE="1")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(str(self.artifact(apple)) + ".provenance.json").exists())
        self.assertNotIn("aggregate", [e["event"] for e in self.events()])

    def test_warm_verified_outputs_remain_reusable(self):
        for apple in (False, True):
            first = self.run_build(apple)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            receipt = Path(str(self.artifact(apple)) + ".provenance.json")
            before = receipt.read_bytes()
            second = self.run_build(apple, STALE="1")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(before, receipt.read_bytes())
        self.assertEqual(sum(e["event"] == "aggregate" for e in self.events()), 1)

    def test_ci_rejects_dirty_source_before_build_local_records_it(self):
        (self.repo / "tracked.txt").write_text("local work\n")
        rejected = self.run_build(CI="true")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual([e["event"] for e in self.events()], ["configure"])
        accepted = self.run_build()
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        receipt = json.loads(Path(str(self.artifact()) + ".provenance.json").read_text())
        self.assertFalse(receipt["source"]["clean"])
        self.assertIsNotNone(receipt["source"]["dirtySha256"])

    def test_source_mutation_during_build_and_missing_compiler_cache_fail(self):
        for flags in ({"BAD_CACHE": "1"}, {"MUTATE_SOURCE": "1"}):
            result = self.run_build(**flags)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(str(self.artifact()) + ".provenance.json").exists())

    def test_aggregate_failure_preserves_previous_binary_and_receipt(self):
        first = self.run_build(True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        artifact = self.artifact(True)
        receipt = Path(str(artifact) + ".provenance.json")
        before = artifact.read_bytes(), receipt.read_bytes()
        result = self.run_build(True, FAIL_AGGREGATE="1")
        self.assertEqual(result.returncode, 44)
        self.assertEqual((artifact.read_bytes(), receipt.read_bytes()), before)
        self.assertEqual(list(artifact.parent.glob("*.pending.*")), [])


if __name__ == "__main__":
    unittest.main()
