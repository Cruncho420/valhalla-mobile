"""Test packaging order and failures in owned temporary build trees.

The verifier stand-in exercises orchestration only; native provenance has separate tests.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class AndroidPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="valhalla-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/move_android_so.sh", self.root / "scripts/move_android_so.sh")
        (self.root / "scripts/verify_native_prebuilt.py").write_text(
            'import os, pathlib, sys\n'
            'p=pathlib.Path("verify-count"); n=int(p.read_text())+1 if p.exists() else 1\n'
            'p.write_text(str(n))\n'
            'sys.exit(29 if n==int(os.environ.get("FAIL_VERIFY_AT", "0")) else 0)\n')
        self.source = self.root / "build/android/arm64-v8a/wrapper/wrapper"
        self.source.mkdir(parents=True)
        self.destination = self.root / "android/valhalla/src/main/jniLibs/arm64-v8a"
        self.binary = "libvalhalla-wrapper.so"
        self.receipt = self.binary + ".provenance.json"

    def provide(self):
        (self.source / self.binary).write_bytes(b"new native output")
        (self.source / self.receipt).write_text("new receipt")

    def run_package(self, *args, failure=0):
        return subprocess.run(["bash", "scripts/move_android_so.sh", *args], cwd=self.root,
                              env={**os.environ, "FAIL_VERIFY_AT": str(failure)},
                              capture_output=True, text=True)

    def test_missing_binary_fails(self):
        result = self.run_package("arm64-v8a")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_missing_receipt_fails(self):
        (self.source / self.binary).write_bytes(b"unverified")
        self.assertNotEqual(self.run_package("arm64-v8a").returncode, 0)
        self.assertFalse(self.destination.exists())

    def test_success_preserves_source_and_installs_pair(self):
        self.provide()
        result = self.run_package("arm64-v8a")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (self.binary, self.receipt):
            self.assertEqual((self.source / name).read_bytes(), (self.destination / name).read_bytes())
        self.assertEqual((self.root / "verify-count").read_text(), "2")

    def test_verifier_failure_preserves_existing_pair(self):
        self.provide()
        self.destination.mkdir(parents=True)
        for name in (self.binary, self.receipt):
            (self.destination / name).write_bytes(b"previous")
        for failure in (1, 2):
            with self.subTest(failure=failure):
                count = self.root / "verify-count"
                if count.exists():
                    count.unlink()
                self.assertEqual(self.run_package("arm64-v8a", failure=failure).returncode, 29)
                for name in (self.binary, self.receipt):
                    self.assertEqual((self.destination / name).read_bytes(), b"previous")
                self.assertFalse(list((self.root / "build/android/arm64-v8a").glob(".native-install.*")))

    def test_bad_architecture_and_extra_arguments_fail(self):
        for arguments in (("bogus",), ("arm64-v8a", "extra")):
            self.assertNotEqual(self.run_package(*arguments).returncode, 0)
            self.assertFalse(self.destination.exists())

    def test_all_architectures_cannot_succeed_with_missing_inputs(self):
        self.provide()
        self.assertNotEqual(self.run_package().returncode, 0)


if __name__ == "__main__":
    unittest.main()
