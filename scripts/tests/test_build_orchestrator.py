"""Verify cleanup and failure propagation without invoking native builds.

Each test owns a temporary checkout with inert platform scripts.
The real workspace and its build caches are never cleanup targets.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "build.sh"


class BuildOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="valhalla-build-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copyfile(SCRIPT, self.root / "build.sh")
        (self.root / "vcpkg").mkdir()
        (self.root / "scripts").mkdir()
        for platform in ("apple", "android", "other"):
            target = self.root / "build" / platform
            target.mkdir(parents=True)
            (target / "keep").write_text("existing build\n")
        for name in ("build_apple", "build_android", "move_android_so", "create_xcframework"):
            self.stub(name, 0)

    def stub(self, name, exit_code):
        target = self.root / "scripts" / (name + ".sh")
        target.write_text(f'#!/bin/bash\nprintf "%s\\n" "{name}" >> calls\nexit {exit_code}\n')
        target.chmod(0o755)

    def run_build(self, *arguments):
        # A caller's unrelated shell variable must never request cleanup.
        return subprocess.run(
            ["bash", "build.sh", *arguments], cwd=self.root,
            env={**os.environ, "clean_all": "true"}, capture_output=True, text=True,
        )

    def assert_kept(self, *platforms):
        for platform in platforms:
            self.assertTrue((self.root / "build" / platform / "keep").exists(), platform)

    def test_regular_build_preserves_every_cache(self):
        result = self.run_build("--ios", "arm64-ios-simulator")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_kept("apple", "android", "other")

    def test_ios_clean_only_removes_apple(self):
        self.assertEqual(self.run_build("ios", "clean").returncode, 0)
        self.assertFalse((self.root / "build/apple").exists())
        self.assert_kept("android", "other")

    def test_android_clean_only_removes_android(self):
        self.assertEqual(self.run_build("android", "clean").returncode, 0)
        self.assertFalse((self.root / "build/android").exists())
        self.assert_kept("apple", "other")

    def test_explicit_all_clean_removes_build(self):
        self.assertEqual(self.run_build("all", "clean").returncode, 0)
        self.assertFalse((self.root / "build").exists())

    def test_invalid_arguments_never_clean_or_build(self):
        for arguments in ((), ("clean",), ("--ios",), ("--android",),
                          ("--ios", "wrong", "clean"),
                          ("--android", "arm64-ios", "clean"),
                          ("unknown", "clean")):
            with self.subTest(arguments=arguments):
                result = self.run_build(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assert_kept("apple", "android", "other")
                self.assertFalse((self.root / "calls").exists())

    def test_native_failure_stops_packaging(self):
        self.stub("build_android", 23)
        result = self.run_build("--android", "arm64-v8a")
        self.assertEqual(result.returncode, 23)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["build_android"])
        self.assert_kept("apple", "android", "other")

    def test_missing_environment_exits_with_help(self):
        (self.root / "vcpkg").rmdir()
        environment = dict(os.environ)
        environment.pop("VCPKG_ROOT", None)
        result = subprocess.run(["bash", "build.sh", "ios"], cwd=self.root,
                                env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("export a custom $VCPKG_ROOT", result.stdout)
        self.assertNotIn("unbound variable", result.stderr)
        self.assert_kept("apple", "android", "other")


if __name__ == "__main__":
    unittest.main()
