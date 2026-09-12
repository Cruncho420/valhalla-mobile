"""Real packaging/verifier integration with Git and bounded ELF fixtures, not JNI execution."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import test_verify_native_prebuilt as fixtures

ROOT = Path(__file__).resolve().parents[2]


class PackagingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PrebuiltTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in ("move_android_so.sh", "verify_native_prebuilt.py",
                     "native_artifact_provenance.py"):
            shutil.copy(ROOT / "scripts" / name, scripts / name)
        self.destination = self.fixture.binary
        source = self.repo / "build/android/arm64-v8a/wrapper/wrapper"
        source.mkdir(parents=True)
        self.fixture.binary.rename(source / self.destination.name)
        self.fixture.receipt.unlink()
        self.fixture.binary = source / self.destination.name
        self.fixture.receipt = Path(str(self.fixture.binary) + ".provenance.json")
        self.fixture.emit()

    def package(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GITHUB_ACTIONS", "GITHUB_SHA")}
        return subprocess.run(["bash", "scripts/move_android_so.sh", "arm64-v8a"],
                              cwd=self.repo, env=env, capture_output=True, text=True)

    def test_install_and_warm_repeat_keep_verifiable_source(self):
        for _ in range(2):
            result = self.package()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(self.fixture.verify(binary=self.destination))
            self.assertEqual(self.destination.read_bytes(), self.fixture.binary.read_bytes())
            self.assertTrue(self.fixture.receipt.is_file())

    def test_changed_source_blocks_replacement_of_installed_pair(self):
        self.assertEqual(self.package().returncode, 0)
        old = self.destination.read_bytes()
        receipt = Path(str(self.destination) + ".provenance.json")
        old_receipt = receipt.read_bytes()
        (self.repo / "tracked.txt").write_text("changed after build\n")
        self.assertNotEqual(self.package().returncode, 0)
        self.assertEqual(self.destination.read_bytes(), old)
        self.assertEqual(receipt.read_bytes(), old_receipt)

    def test_corrupt_binary_is_not_installed(self):
        self.fixture.binary.write_bytes(self.fixture.binary.read_bytes() + b"corrupt")
        self.assertNotEqual(self.package().returncode, 0)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
