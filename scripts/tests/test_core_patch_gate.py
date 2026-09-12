"""Exercise the actual CMake gate with isolated real Git repositories, never the live submodule.
Only the expected core commit is rebound to each synthetic fixture's real commit.
The original source, patch, both file hashes, Git commands, and CMake helper remain genuine.
"""
from pathlib import Path
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CORE_PIN = "e2f017b16080f49203de245a211b09efab09cf72"
GIT = shutil.which("git")
CMAKE = shutil.which("cmake")


def run(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


def git(root, *args):
    result = run(GIT, "-C", str(root), *args)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


class PatchGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="valhalla-core-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.core = self.root / "src/valhalla"
        self.target = self.core / "src/meili/match_route.cc"
        self.target.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "cmake", self.root / "cmake")
        shutil.copytree(ROOT / "patches/valhalla", self.root / "patches/valhalla")
        shutil.copyfile(ROOT / "scripts/tests/fixtures/match_route.cc", self.target)
        (self.core / "other.txt").write_text("untouched\n")
        for repo in (self.root, self.core):
            git(repo, "init", "--quiet")
            git(repo, "config", "user.email", "fixture@example.invalid")
            git(repo, "config", "user.name", "Fixture")
        git(self.core, "add", ".")
        git(self.core, "commit", "-qm", "fixture")
        self.pin = git(self.core, "rev-parse", "HEAD")
        manifest = self.root / "patches/valhalla/manifest.cmake"
        manifest.write_text(manifest.read_text().replace(CORE_PIN, self.pin))
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture wrapper")

    def gate(self, git_executable=None):
        args = [CMAKE]
        if git_executable:
            args += ["-DGIT_EXECUTABLE=" + str(git_executable)]
        return run(*args, "-P", str(self.root / "cmake/PrepareValhalla.cmake"))

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(),
                         "8f8db9a3c5725c4c5239d6f2a3d8ec5846d4847b08c2f755fdbc5a0d65dcd858")

    def rejected(self, result, expected):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def test_clean_repeat_and_concurrent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.gate(), range(4)))
        for result in results:
            self.success(result)
        before = self.target.stat().st_mtime_ns
        self.success(self.gate())
        self.assertEqual(before, self.target.stat().st_mtime_ns)

    def test_wrong_core_head(self):
        git(self.core, "commit", "--allow-empty", "-qm", "wrong pin")
        self.rejected(self.gate(), "core HEAD or parent gitlink")

    def test_wrong_parent_index(self):
        git(self.root, "update-index", "--cacheinfo", "160000", "1" * 40, "src/valhalla")
        self.rejected(self.gate(), "core HEAD or parent gitlink")

    def test_patch_tamper(self):
        patch = self.root / "patches/valhalla/0001-meili-stateful-terminal-segment.patch"
        patch.write_text(patch.read_text() + "\n")
        self.rejected(self.gate(), "patch checksum mismatch")

    def test_unexpected_target(self):
        self.target.write_text(self.target.read_text() + "\n")
        before = self.target.read_bytes()
        self.rejected(self.gate(), "core source checksum mismatch")
        self.assertEqual(before, self.target.read_bytes())

    def test_dirty_other_and_staged_changes(self):
        other = self.core / "other.txt"
        other.write_text("unexpected\n")
        self.rejected(self.gate(), "unexpected tracked core changes")
        git(self.core, "add", "other.txt")
        self.rejected(self.gate(), "unexpected staged or file-mode changes")

    def test_target_mode_change(self):
        self.target.chmod(0o755)
        self.rejected(self.gate(), "unexpected staged or file-mode changes")

    def test_untracked_core_source(self):
        (self.core / "extra.cc").write_text("// unexpected build input\n")
        self.rejected(self.gate(), "unexpected untracked core files")

    def test_apply_failure_does_not_mutate(self):
        proxy = self.root / "git-fail-apply"
        for phase in ("check", "apply"):
            with self.subTest(phase=phase):
                proxy.write_text('#!/bin/bash\napply=false; check=false\n'
                                 'for arg in "$@"; do\n'
                                 '  [ "$arg" != "apply" ] || apply=true\n'
                                 '  [ "$arg" != "--check" ] || check=true\ndone\n'
                                 f'if $apply && {{ [ "{phase}" = "check" ] || ! $check; }}; then exit 43; fi\n'
                                 f'exec "{GIT}" "$@"\n')
                proxy.chmod(0o755)
                before = self.target.read_bytes()
                self.rejected(self.gate(proxy), "Git verification failed")
                self.assertEqual(before, self.target.read_bytes())

    def test_missing_initialized_core(self):
        (self.core / ".git").rename(self.core / "fixture-git-hidden")
        self.rejected(self.gate(), "initialized regular source")

    def test_directory_failure_prevents_configuration(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        calls = self.root / "cmake-calls"
        for name, text in {
            "mkdir": "#!/bin/bash\nexit 41\n",
            "xcodebuild": "#!/bin/bash\nexit 0\n",
            "cmake": '#!/bin/bash\nprintf "%s\n" "$*" >> "$CALL_LOG"\nexit 0\n',
        }.items():
            executable = binaries / name
            executable.write_text(text)
            executable.chmod(0o755)
        env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
               "VCPKG_ROOT": str(self.root / "vcpkg"),
               "ANDROID_NDK_HOME": str(self.root / "ndk"), "CALL_LOG": str(calls)}
        for script, arch in (("build_apple.sh", "arm64-ios-simulator"),
                             ("build_android.sh", "arm64-v8a")):
            with self.subTest(script=script):
                calls.write_text("")
                result = run("bash", str(ROOT / "scripts" / script), arch,
                             cwd=self.root, env=env)
                self.assertEqual(result.returncode, 41, result.stdout + result.stderr)
                self.assertEqual(calls.read_text(), "")

    def test_paths_with_spaces_remain_single_arguments(self):
        work = (self.root / "work space").resolve()
        work.mkdir()
        binaries = self.root / "bin"
        binaries.mkdir()
        calls = self.root / "arguments"
        for name, text in {
            "xcodebuild": "#!/bin/bash\nexit 0\n",
            "cmake": '#!/bin/bash\nprintf "%s\n" "$@" > "$CALL_LOG"\nexit 37\n',
        }.items():
            executable = binaries / name
            executable.write_text(text)
            executable.chmod(0o755)
        env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
               "VCPKG_ROOT": str(work / "vcpkg tools"),
               "ANDROID_NDK_HOME": str(work / "ndk tools"), "CALL_LOG": str(calls)}
        for script, arch in (("build_apple.sh", "arm64-ios-simulator"),
                             ("build_android.sh", "arm64-v8a")):
            with self.subTest(script=script):
                result = run("bash", str(ROOT / "scripts" / script), arch, cwd=work, env=env)
                self.assertEqual(result.returncode, 37)
                arguments = calls.read_text().splitlines()
                self.assertIn("-DCMAKE_TOOLCHAIN_FILE=" + env["VCPKG_ROOT"] +
                              "/scripts/buildsystems/vcpkg.cmake", arguments)
                self.assertIn("-DVCPKG_OVERLAY_TRIPLETS=" + str(work / "triplets"), arguments)
                self.assertEqual(arguments[arguments.index("-S") + 1], str(work / "src"))
                if script == "build_android.sh":
                    self.assertIn("-DVCPKG_CHAINLOAD_TOOLCHAIN_FILE=" + env["ANDROID_NDK_HOME"] +
                                  "/build/cmake/android.toolchain.cmake", arguments)

    def test_configure_failure_prevents_native_build(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        calls = self.root / "cmake-calls"
        cmake = binaries / "cmake"
        cmake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                         'if [ "$1" != "--build" ]; then exit 37; fi\nexit 0\n')
        cmake.chmod(0o755)
        xcode = binaries / "xcodebuild"
        xcode.write_text("#!/bin/bash\nexit 0\n")
        xcode.chmod(0o755)
        env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
               "VCPKG_ROOT": str(self.root / "vcpkg"),
               "ANDROID_NDK_HOME": str(self.root / "ndk"), "CALL_LOG": str(calls)}
        for script, arch in (("build_apple.sh", "arm64-ios-simulator"),
                             ("build_android.sh", "arm64-v8a")):
            with self.subTest(script=script):
                calls.write_text("")
                result = run("bash", str(ROOT / "scripts" / script), arch,
                             cwd=self.root, env=env)
                self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
                self.assertEqual(len(calls.read_text().splitlines()), 1)
                self.assertNotIn("--build", calls.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
