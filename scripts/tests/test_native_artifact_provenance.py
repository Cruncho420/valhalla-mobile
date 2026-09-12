"""PURPOSE: Exercise provenance trust and failure boundaries without native builds.
RESPONSIBILITY: Use isolated real git repositories and small format fixtures.
DEPENDENCIES: unittest, git, and the provenance utility.
CONSUMERS: Standalone build/packaging regression checks.
"""
import argparse
import contextlib
import importlib.util
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

SPEC = importlib.util.spec_from_file_location(
    "provenance", Path(__file__).resolve().parents[1] / "native_artifact_provenance.py")
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class ProvenanceTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)

    def init(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.name", "Fixture")
        self.git(root, "config", "user.email", "fixture@example.invalid")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.core = self.repo / P.CORE_PATH
        self.init(self.repo)
        self.init(self.core)
        target = self.core / P.TARGET_PATH
        target.parent.mkdir(parents=True)
        target.write_text("original\n")
        original = P.file_hash(target)
        self.git(self.core, "add", ".")
        self.git(self.core, "commit", "-qm", "Core fixture")
        core_revision = self.git(self.core, "rev-parse", "HEAD").decode().strip()
        patch = self.repo / P.PATCH_PATH
        patch.parent.mkdir(parents=True)
        patch.write_text("test patch bytes\n")
        self.manifest = patch.parent / "manifest.cmake"
        patched = P.hashlib.sha256(b"patched\n").hexdigest()
        self.manifest.write_text("\n".join('set(%s "%s")' % item for item in [
            ("expected_core", core_revision), ("original_sha", original),
            ("patched_sha", patched), ("patch_sha", P.file_hash(patch))]) + "\n")
        (self.repo / "tracked.txt").write_text("clean\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-qm", "Wrapper fixture")
        target.write_text("patched\n")
        self.source = self.root / "source.json"
        P.atomic_write(self.source, P.snapshot(self.repo, self.manifest, True))
        self.binary = self.root / "libvalhalla-wrapper.so"
        self.binary.write_bytes(self.elf())
        self.compiler = self.root / "compiler"
        self.compiler.write_text("#!/bin/sh\nprintf 'Fixture compiler 1.0\\n'\n")
        self.compiler.chmod(0o755)
        self.receipt = self.root / "artifact.json"
        self.args = argparse.Namespace(source=self.source, repo=self.repo, manifest=self.manifest,
            require_clean=True, binary=self.binary, compiler=self.compiler, toolchain_id="fixture-ndk",
            abi="arm64-v8a", output=self.receipt, replace=False)

    def elf(self, bits=2, machine=183, kind=3):
        data = bytearray(64)
        data[:7] = b"\x7fELF" + bytes([bits, 1, 1])
        struct.pack_into("<HH", data, 16, kind, machine)
        return bytes(data)

    def verify(self, receipt=None, expected=None, abi=None):
        return P.verify(self.binary, abi or "arm64-v8a", receipt or P.load_json(self.receipt),
                        expected or P.source_from(P.load_json(self.source)))

    def test_clean_roundtrip_and_canonical_snapshot(self):
        receipt = P.emit(self.args)
        self.assertEqual(self.verify(), receipt)
        self.assertTrue(receipt["source"]["clean"])
        self.assertEqual(self.source.read_bytes(), P.canonical(P.snapshot(self.repo, self.manifest, True)))
        self.assertEqual(self.receipt.read_bytes(), P.canonical(receipt))

    def test_packaging_without_core_requires_external_ci_expectation(self):
        P.emit(self.args)
        expected = P.source_from(P.load_json(self.source))
        self.core.rename(self.root / "absent-core")
        args = ["verify", "--binary", str(self.binary), "--abi", "arm64-v8a", "--receipt",
                str(self.receipt), "--expected-wrapper-revision", expected["wrapperRevision"],
                "--manifest", str(self.manifest), "--require-clean"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(P.main(args), 0)
            wrong = args.copy()
            wrong[wrong.index("--expected-wrapper-revision") + 1] = "0" * 40
            self.assertEqual(P.main(wrong), 1)
            self.assertEqual(P.main(args[:-1]), 1)

    def test_dirty_snapshot_identifies_bytes_and_clean_policy_rejects(self):
        (self.repo / "tracked.txt").write_text("dirty one\n")
        one = P.snapshot(self.repo, self.manifest)
        (self.repo / "tracked.txt").write_text("dirty two\n")
        two = P.snapshot(self.repo, self.manifest)
        self.assertFalse(one["source"]["clean"])
        self.assertNotEqual(one["source"]["buildSourceIdentity"], two["source"]["buildSourceIdentity"])
        with self.assertRaises(P.ProvenanceError):
            P.snapshot(self.repo, self.manifest, True)

    def test_changed_source_during_build_refuses_without_output(self):
        (self.repo / "new-input.cc").write_text("new source\n")
        with self.assertRaises(P.ProvenanceError):
            P.emit(self.args)
        self.assertFalse(self.receipt.exists())

    def test_changed_core_or_patch_is_rejected(self):
        (self.core / P.TARGET_PATH).write_text("wrong patch\n")
        with self.assertRaises(P.ProvenanceError):
            P.snapshot(self.repo, self.manifest)
        (self.core / P.TARGET_PATH).write_text("patched\n")
        (self.repo / P.PATCH_PATH).write_text("wrong patch artifact\n")
        with self.assertRaises(P.ProvenanceError):
            P.snapshot(self.repo, self.manifest)

    def test_prebuilt_without_receipt_is_not_attested(self):
        os.utime(self.binary, ns=(1, 1))
        with self.assertRaises(OSError):
            P.emit(self.args)
        self.assertFalse(self.receipt.exists())

    def test_warm_binary_reuses_only_same_source_and_toolchain(self):
        receipt = P.emit(self.args)
        os.utime(self.binary, ns=(1, 1))
        self.assertEqual(P.emit(self.args), receipt)
        self.args.toolchain_id = "different-ndk"
        with self.assertRaises(P.ProvenanceError):
            P.emit(self.args)

    def test_binary_tampering_and_mixed_source_fail(self):
        receipt = P.emit(self.args)
        self.binary.write_bytes(self.elf() + b"tampered")
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt)
        self.binary.write_bytes(self.elf())
        expected = P.expected_source("0" * 40, P.manifest(self.manifest))
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt, expected)

    def test_wrong_patch_expectation_cannot_trust_receipt_itself(self):
        receipt = P.emit(self.args)
        pins = P.manifest(self.manifest)
        pins["patch_sha"] = "0" * 64
        expected = P.expected_source(receipt["source"]["wrapperRevision"], pins)
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt, expected)

    def test_wrong_abi_in_bytes_and_in_metadata_fail(self):
        receipt = P.emit(self.args)
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt, abi="x86_64")
        receipt["artifact"]["abi"] = "x86_64"
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt, abi="x86_64")

    def test_elf_shared_type_and_each_supported_abi(self):
        for abi, (bits, machine) in P.ELF_ABIS.items():
            self.binary.write_bytes(self.elf(bits, machine))
            P.binary_abi(self.binary, abi)
        self.binary.write_bytes(self.elf(kind=2))
        with self.assertRaises(P.ProvenanceError):
            P.binary_abi(self.binary, "arm64-v8a")

    def test_apple_platform_not_only_architecture(self):
        with mock.patch.object(P, "command", side_effect=[b"arm64 x86_64\n", b" platform 7\n"]):
            P.binary_abi(self.binary, "arm64-ios-simulator")
        for loads in (b"platform 2\n", b"platform 2\nplatform 7\n", b""):
            with mock.patch.object(P, "command", side_effect=[b"arm64\n", loads]):
                with self.assertRaises(P.ProvenanceError):
                    P.binary_abi(self.binary, "arm64-ios-simulator")
        with mock.patch.object(P, "command", side_effect=[b"x86_64\n"]):
            with self.assertRaises(P.ProvenanceError):
                P.binary_abi(self.binary, "arm64-ios-simulator")

    def test_atomic_failure_preserves_prior_receipt_and_removes_own_temp(self):
        receipt = P.emit(self.args)
        before = self.receipt.read_bytes()
        with mock.patch.object(P.os, "fsync", side_effect=OSError("injected flush failure")):
            with self.assertRaises(OSError):
                P.atomic_write(self.receipt, receipt, replace=True)
        self.assertEqual(self.receipt.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".artifact.json.*")), [])

    def test_explicit_same_owner_replacement_only(self):
        receipt = P.emit(self.args)
        with self.assertRaises(P.ProvenanceError):
            P.atomic_write(self.receipt, receipt)
        P.atomic_write(self.receipt, receipt, replace=True)
        receipt["artifact"]["abi"] = "x86_64"
        with self.assertRaises(P.ProvenanceError):
            P.atomic_write(self.receipt, receipt, replace=True)

    def test_malformed_duplicate_and_symlink_receipts_fail(self):
        self.receipt.write_text('{"kind":1,"kind":2}')
        with self.assertRaises(P.ProvenanceError):
            P.load_json(self.receipt)
        link = self.root / "link.json"
        link.symlink_to(self.receipt)
        with self.assertRaises(P.ProvenanceError):
            P.load_json(link)
        with self.assertRaises(P.ProvenanceError):
            P.atomic_write(link, {})

    def test_receipt_cannot_be_its_own_expected_source(self):
        P.emit(self.args)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(P.main(["verify", "--binary", str(self.binary), "--abi", "arm64-v8a",
                "--receipt", str(self.receipt), "--expected-source", str(self.receipt)]), 1)

    def test_untracked_core_input_and_staged_patch_are_not_clean(self):
        extra = self.core / "extra.cc"
        extra.write_text("extra input\n")
        self.assertFalse(P.snapshot(self.repo, self.manifest)["source"]["clean"])
        with self.assertRaises(P.ProvenanceError):
            P.snapshot(self.repo, self.manifest, True)
        extra.rename(self.root / "owned-extra.cc")
        self.git(self.core, "add", P.TARGET_PATH)
        self.assertFalse(P.snapshot(self.repo, self.manifest)["source"]["clean"])
        with self.assertRaises(P.ProvenanceError):
            P.snapshot(self.repo, self.manifest, True)

    def test_exempt_patched_target_mode_is_still_source_identity(self):
        target = self.core / P.TARGET_PATH
        target.chmod(0o755)
        self.assertFalse(P.snapshot(self.repo, self.manifest)["source"]["clean"])

    def test_malformed_source_boolean_and_receipt_shapes(self):
        receipt = P.emit(self.args)
        receipt["source"]["clean"] = 1
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt)
        receipt["source"]["clean"] = True
        receipt["artifact"] = []
        with self.assertRaises(P.ProvenanceError):
            self.verify(receipt)

    def test_replacement_refuses_intervening_foreign_write(self):
        receipt = P.emit(self.args)
        real_sync = P.os.fsync
        def mutate_then_sync(descriptor):
            self.receipt.write_text('{"foreign":"preserve"}')
            real_sync(descriptor)
        with mock.patch.object(P.os, "fsync", side_effect=mutate_then_sync):
            with self.assertRaises(P.ProvenanceError):
                P.atomic_write(self.receipt, receipt, replace=True)
        self.assertEqual(self.receipt.read_text(), '{"foreign":"preserve"}')

    def test_parent_sync_failure_leaves_complete_reviewable_receipt(self):
        receipt = P.emit(self.args)
        real_sync = P.os.fsync
        calls = []
        def fail_parent(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError("injected directory flush failure")
            real_sync(descriptor)
        with mock.patch.object(P.os, "fsync", side_effect=fail_parent):
            with self.assertRaises(OSError):
                P.atomic_write(self.receipt, receipt, replace=True)
        self.assertEqual(self.verify(), receipt)

    def test_external_tool_output_and_time_are_bounded(self):
        with mock.patch.object(P, "MAX_COMMAND", 1024):
            with self.assertRaises(P.ProvenanceError):
                P.command([sys.executable, "-c", "print('x'*2048)"])
        with mock.patch.object(P, "COMMAND_SECONDS", 0.05):
            with self.assertRaises(P.ProvenanceError):
                P.command([sys.executable, "-c", "import time; time.sleep(2)"])

    def test_malformed_existing_owner_is_not_overwritten(self):
        receipt = P.emit(self.args)
        self.receipt.write_text('{"kind":"valhalla-native-artifact","schemaVersion":1,"artifact":[]}')
        before = self.receipt.read_bytes()
        with self.assertRaises(P.ProvenanceError):
            P.atomic_write(self.receipt, receipt, replace=True)
        self.assertEqual(self.receipt.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
