"""Exercise real Git/source/receipt validation with small ELF format fixtures, not native builds."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_native_prebuilt as guard
P = guard.provenance


class PrebuiltTests(unittest.TestCase):
    def git(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)

    def init(self, path):
        path.mkdir(parents=True, exist_ok=True)
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Fixture")
        self.git(path, "config", "user.email", "fixture@example.invalid")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="prebuilt-guard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.core = self.repo / P.CORE_PATH
        self.init(self.repo)
        self.init(self.core)
        self.target = self.core / P.TARGET_PATH
        self.target.parent.mkdir(parents=True)
        self.target.write_text("original\n")
        original = P.file_hash(self.target)
        self.git(self.core, "add", ".")
        self.git(self.core, "commit", "-qm", "core")
        core_revision = self.git(self.core, "rev-parse", "HEAD").decode().strip()
        patch = self.repo / P.PATCH_PATH
        patch.parent.mkdir(parents=True)
        patch.write_text("fixture patch\n")
        self.manifest = patch.parent / "manifest.cmake"
        self.manifest.write_text("\n".join(f'set({k} "{v}")' for k, v in {
            "expected_core": core_revision, "original_sha": original,
            "patched_sha": P.hashlib.sha256(b"patched\n").hexdigest(),
            "patch_sha": P.file_hash(patch)}.items()) + "\n")
        (self.repo / ".gitignore").write_text("*.so\n*.so.provenance.json\n/build/\n")
        (self.repo / "tracked.txt").write_text("clean\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "wrapper")
        self.revision = self.git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.target.write_text("patched\n")
        self.binary = self.repo / "android/valhalla/src/main/jniLibs/arm64-v8a/libvalhalla-wrapper.so"
        self.binary.parent.mkdir(parents=True)
        data = bytearray(64)
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HH", data, 16, 3, 183)
        self.binary.write_bytes(data)
        self.receipt = Path(str(self.binary) + ".provenance.json")
        self.compiler = self.root / "compiler"
        self.compiler.write_text("#!/bin/sh\nprintf 'Fixture compiler 1.0\\n'\n")
        self.compiler.chmod(0o755)
        self.emit()

    def emit(self):
        source = self.root / "source.json"
        source.write_bytes(P.canonical(P.snapshot(self.repo, self.manifest)))
        # Simulate a completed native output after the pre-build source snapshot.
        tick = source.stat().st_mtime_ns + 1
        os.utime(self.binary, ns=(tick, tick))
        P.emit(argparse.Namespace(source=source, repo=self.repo, manifest=self.manifest,
               require_clean=False, binary=self.binary, compiler=self.compiler,
               toolchain_id="fixture-ndk", abi="arm64-v8a", output=self.receipt,
               replace=self.receipt.exists()))

    def verify(self, revision=None, binary=None, abi="arm64-v8a"):
        return guard.verify_prebuilt(self.repo, abi, revision, binary)

    def without_core(self):
        self.core.rename(self.root / "saved-core")

    def test_local_clean_and_dirty_warm_cache(self):
        self.assertTrue(self.verify())
        (self.repo / "tracked.txt").write_text("local change\n")
        with self.assertRaises(P.ProvenanceError):
            self.verify()
        self.emit()
        self.assertTrue(self.verify())

    def test_clean_packaging_without_core(self):
        self.without_core()
        self.assertTrue(self.verify(self.revision))
        with self.assertRaises(P.ProvenanceError):
            self.verify("1" * 40)

    def test_no_core_changed_and_untracked_wrapper_rejected(self):
        self.without_core()
        (self.repo / "tracked.txt").write_text("changed\n")
        with self.assertRaises(P.ProvenanceError):
            self.verify()
        (self.repo / "tracked.txt").write_text("clean\n")
        (self.repo / "new-source.cc").write_text("untracked\n")
        with self.assertRaises(P.ProvenanceError):
            self.verify()

    def test_no_core_uninitialized_source_files_rejected(self):
        self.without_core()
        self.core.mkdir()
        (self.core / "unexpected.cc").write_text("unverified\n")
        with self.assertRaises(P.ProvenanceError):
            self.verify()

    def test_missing_binary_allows_build_but_missing_receipt_does_not(self):
        self.receipt.unlink()
        with self.assertRaises(OSError):
            self.verify()
        self.binary.unlink()
        self.assertFalse(self.verify())

    def test_tampered_binary_and_wrong_abi_rejected(self):
        with self.assertRaises(P.ProvenanceError):
            self.verify(binary=self.binary, abi="x86_64")
        self.binary.write_bytes(self.binary.read_bytes() + b"changed")
        with self.assertRaises(P.ProvenanceError):
            self.verify()

    def test_wrong_source_and_malformed_receipt_rejected(self):
        receipt = P.load_json(self.receipt)
        receipt["source"] = P.expected_source("2" * 40, P.manifest(self.manifest))
        self.receipt.write_bytes(P.canonical(receipt))
        with self.assertRaises(P.ProvenanceError):
            self.verify()
        self.receipt.write_text("{}")
        with self.assertRaises(P.ProvenanceError):
            self.verify()

    def test_invalid_current_core_rejected_before_reuse(self):
        self.target.write_text("corrupted\n")
        with self.assertRaises(P.ProvenanceError):
            self.verify()

    def test_cli_missing_ci_revision_and_status_codes(self):
        args = ["--repo", str(self.repo), "--abi", "arm64-v8a"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
                self.assertEqual(guard.main(args), 1)
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_SHA": self.revision}, clear=True):
                self.assertEqual(guard.main(args), 0)
                self.receipt.write_text("{}")
                self.assertEqual(guard.main(args), 1)
                self.binary.unlink()
                self.assertEqual(guard.main(args), 10)

    def test_apple_triplet_delegates_to_platform_binary_probe(self):
        # Only platform ABI probing is stood in; source/receipt/hash checks remain real.
        apple = self.root / "libvalhalla_all.a"
        apple.write_bytes(self.binary.read_bytes())
        receipt = P.load_json(self.receipt)
        receipt["artifact"]["name"] = apple.name
        receipt["artifact"]["abi"] = "arm64-ios-simulator"
        Path(str(apple) + ".provenance.json").write_bytes(P.canonical(receipt))
        with mock.patch.object(P, "binary_abi") as probe:
            self.assertTrue(self.verify(binary=apple, abi="arm64-ios-simulator"))
            probe.assert_called_once_with(apple, "arm64-ios-simulator")

    def test_explicit_binary_path_uses_adjacent_receipt(self):
        self.assertTrue(self.verify(binary=self.binary))
        with self.assertRaises(P.ProvenanceError):
            self.verify(abi="arm64-ios")


if __name__ == "__main__":
    unittest.main(verbosity=2)
