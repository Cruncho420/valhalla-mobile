"""Test Android transport with real Git/receipt authority and tiny ELF-format fixtures.

These are transport/provenance tests, not executable JNI binaries or Android device tests.
Only this new test module is owned by this task; existing assertions remain unchanged.
"""
import argparse
import importlib
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import unittest
from unittest import mock
import warnings
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_verify_native_prebuilt as fixtures

P = fixtures.P
ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
BINARY = "libvalhalla-wrapper.so"
JNI = Path("android/valhalla/src/main/jniLibs")


class AndroidTransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = importlib.import_module("package_android_native")
        self.fixture = fixtures.PrebuiltTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.root = self.fixture.root
        self.inputs = self.root / "inputs"
        self.output = self.root / "native.zip"
        self.environment = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "false", "GITHUB_SHA": self.fixture.revision})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for abi in ABIS:
            self.create_input(abi)
        # These files belong to this disposable fixture, not an operator checkout.
        shutil.rmtree(self.repo / JNI)
        self.expected = {str(JNI / abi / name) for abi in ABIS
                         for name in (BINARY, BINARY + ".provenance.json")}

    def binary(self, abi):
        return self.inputs / ("libvalhalla-" + abi) / BINARY

    def create_input(self, abi):
        binary = self.binary(abi)
        binary.parent.mkdir(parents=True)
        data = bytearray(64)
        elf_class, machine = P.ELF_ABIS[abi]
        data[:7] = b"\x7fELF" + bytes((elf_class, 1, 1))
        struct.pack_into("<HH", data, 16, 3, machine)
        binary.write_bytes(data)
        source = self.root / (abi + "-source.json")
        source.write_bytes(P.canonical(P.snapshot(self.repo, self.fixture.manifest)))
        tick = source.stat().st_mtime_ns + 1
        os.utime(binary, ns=(tick, tick))
        P.emit(argparse.Namespace(source=source, repo=self.repo, manifest=self.fixture.manifest,
               require_clean=False, binary=binary, compiler=self.fixture.compiler,
               toolchain_id="fixture-ndk", abi=abi,
               output=Path(str(binary) + ".provenance.json"), replace=False))

    def combine(self):
        self.transport.combine(self.repo, self.inputs, self.output)
        return self.output

    def inventory(self):
        root = self.repo / JNI
        return {str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*") if p.is_file()} if root.exists() else {}

    def mutate_zip(self, source, transform):
        destination = self.root / "mutated.zip"
        with zipfile.ZipFile(source) as archive:
            entries = [(item, archive.read(item)) for item in archive.infolist()]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(destination, "w") as archive:
                for name, data in transform(entries):
                    archive.writestr(name, data)
        return destination

    def assert_install_rejected(self, archive):
        before = self.inventory()
        with self.assertRaises((P.ProvenanceError, OSError, ValueError, zipfile.BadZipFile)):
            self.transport.install(self.repo, archive)
        self.assertEqual(self.inventory(), before)

    def make_aar(self, mutate=None):
        archive = self.root / "fixture.aar"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("AndroidManifest.xml", b"fixture manifest")
            output.writestr("classes.jar", b"fixture classes")
            for abi in ABIS:
                data = (self.repo / JNI / abi / BINARY).read_bytes()
                output.writestr("jni/" + abi + "/" + BINARY,
                                mutate(abi, data) if mutate else data)
        return archive

    def test_combine_and_install_exact_four_architectures(self):
        archive = self.combine()
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), self.expected)
            self.assertEqual(len(package.infolist()), 8)
            for abi in ABIS:
                for suffix in ("", ".provenance.json"):
                    self.assertEqual(package.read(str(JNI / abi / (BINARY + suffix))),
                                     Path(str(self.binary(abi)) + suffix).read_bytes())
        self.transport.install(self.repo, archive)
        self.assertEqual(set(self.inventory()), {
            str(Path(abi) / name) for abi in ABIS for name in (BINARY, BINARY + ".provenance.json")})
        for abi in ABIS:
            self.assertTrue(fixtures.guard.verify_prebuilt(self.repo, abi))

    def test_combine_missing_architecture_or_receipt_is_not_partial(self):
        for suffix in ("", ".provenance.json"):
            with self.subTest(suffix=suffix):
                target = Path(str(self.binary("x86")) + suffix)
                saved = target.read_bytes()
                target.unlink()
                try:
                    with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
                        self.combine()
                    self.assertFalse(self.output.exists())
                finally:
                    target.write_bytes(saved)

    def test_combine_tampered_binary_rejected(self):
        binary = self.binary("armeabi-v7a")
        binary.write_bytes(binary.read_bytes() + b"tampered")
        with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
            self.combine()
        self.assertFalse(self.output.exists())

    def test_combine_wrong_abi_even_with_matching_hash_rejected(self):
        binary = self.binary("x86")
        binary.write_bytes(self.binary("arm64-v8a").read_bytes())
        path = Path(str(binary) + ".provenance.json")
        receipt = P.load_json(path)
        receipt["artifact"]["sha256"] = P.file_hash(binary)
        receipt["artifact"]["size"] = binary.stat().st_size
        path.write_bytes(P.canonical(receipt))
        with self.assertRaises(P.ProvenanceError):
            self.combine()
        self.assertFalse(self.output.exists())

    def test_existing_zip_is_preserved(self):
        self.output.write_bytes(b"operator output")
        with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
            self.combine()
        self.assertEqual(self.output.read_bytes(), b"operator output")

    def test_install_duplicate_member_rejected(self):
        archive = self.mutate_zip(self.combine(), lambda entries: entries + [entries[0]])
        self.assert_install_rejected(archive)

    def test_install_missing_and_unsafe_paths_rejected(self):
        source = self.combine()
        transforms = [lambda rows: rows[:-1],
                      lambda rows: rows[:-1] + [("../escape", rows[-1][1])],
                      lambda rows: rows + [("unexpected.txt", b"foreign")],
                      lambda rows: rows[:-1] + [("/absolute", rows[-1][1])]]
        for transform in transforms:
            with self.subTest(transform=transforms.index(transform)):
                self.assert_install_rejected(self.mutate_zip(source, transform))
        self.assertFalse((self.root / "escape").exists())

    def test_install_symlink_member_rejected(self):
        def symlink(entries):
            name, data = entries[0]
            link = zipfile.ZipInfo(name.filename)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            return [(link, b"foreign-target")] + entries[1:]
        self.assert_install_rejected(self.mutate_zip(self.combine(), symlink))

    def test_install_tampered_fourth_member_leaves_no_first_three(self):
        def tamper(entries):
            return [(item, data + b"changed" if item.filename == str(JNI / "x86" / BINARY) else data)
                    for item, data in entries]
        self.assert_install_rejected(self.mutate_zip(self.combine(), tamper))
        self.assertEqual(self.inventory(), {})

    def test_install_existing_destination_is_preserved(self):
        archive = self.combine()
        destination = self.repo / JNI / "x86" / BINARY
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"operator native file")
        self.assert_install_rejected(archive)
        self.assertEqual(destination.read_bytes(), b"operator native file")

    def test_changed_current_source_cannot_authorize_zip_install(self):
        archive = self.combine()
        (self.repo / "tracked.txt").write_text("new source identity\n")
        self.assert_install_rejected(archive)

    def test_aar_success_binds_bytes_and_refuses_existing_receipt(self):
        self.transport.install(self.repo, self.combine())
        aar = self.make_aar()
        output = self.root / "aar.provenance.json"
        self.transport.verify_aar(self.repo, aar, output)
        receipt = P.load_json(output)
        self.assertEqual(set(receipt), {"schemaVersion", "kind", "aar", "inputs"})
        self.assertEqual(receipt["schemaVersion"], 1)
        self.assertEqual(receipt["kind"], "valhalla-android-package")
        self.assertEqual(receipt["aar"], {"name": aar.name, "sha256": P.file_hash(aar),
                                         "size": aar.stat().st_size})
        self.assertEqual(receipt["inputs"], {
            abi: P.load_json(self.repo / JNI / abi / (BINARY + ".provenance.json")) for abi in ABIS})
        self.assertIn(P.file_hash(aar), P.canonical(receipt).decode())
        for abi in ABIS:
            self.assertIn(P.file_hash(self.repo / JNI / abi / BINARY), P.canonical(receipt).decode())
        before = output.read_bytes()
        with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
            self.transport.verify_aar(self.repo, aar, output)
        self.assertEqual(output.read_bytes(), before)

    def test_aar_tampered_member_or_untrusted_jni_rejected(self):
        self.transport.install(self.repo, self.combine())
        output = self.root / "aar.provenance.json"
        aar = self.make_aar(lambda abi, data: data + b"changed" if abi == "x86" else data)
        with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
            self.transport.verify_aar(self.repo, aar, output)
        self.assertFalse(output.exists())
        aar = self.make_aar()
        (self.repo / JNI / "x86" / (BINARY + ".provenance.json")).unlink()
        with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
            self.transport.verify_aar(self.repo, aar, output)
        self.assertFalse(output.exists())

    def test_aar_duplicate_or_extra_native_member_rejected(self):
        self.transport.install(self.repo, self.combine())
        source = self.make_aar()
        transforms = [lambda rows: rows + [rows[-1]],
                      lambda rows: rows + [("jni/x86/libforeign.so", b"foreign")],
                      lambda rows: rows[:-1]]
        for index, transform in enumerate(transforms):
            with self.subTest(case=index):
                aar = self.mutate_zip(source, transform)
                output = self.root / ("aar-" + str(index) + ".json")
                with self.assertRaises((P.ProvenanceError, OSError, ValueError)):
                    self.transport.verify_aar(self.repo, aar, output)
                self.assertFalse(output.exists())


    def test_aar_changed_after_initial_hash_cannot_publish(self):
        self.transport.install(self.repo, self.combine())
        aar = self.make_aar()
        output = self.root / "changed-aar.json"
        original = P.file_hash
        changed = []

        def change_after_hash(path):
            digest = original(path)
            if Path(path) == aar and not changed:
                changed.append(True)
                with zipfile.ZipFile(aar, "a") as package:
                    package.writestr("added-after-hash.txt", b"changed AAR bytes")
            return digest

        with mock.patch.object(P, "file_hash", side_effect=change_after_hash):
            with self.assertRaises(P.ProvenanceError):
                self.transport.verify_aar(self.repo, aar, output)
        self.assertFalse(output.exists())

    def test_aar_symlink_native_member_is_not_a_regular_native_library(self):
        self.transport.install(self.repo, self.combine())
        source = self.make_aar()

        def symlink(rows):
            result = []
            for info, data in rows:
                if info.filename == "jni/x86/" + BINARY:
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                result.append((info, data))
            return result

        aar = self.mutate_zip(source, symlink)
        output = self.root / "symlink-aar.json"
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, aar, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
