"""Exercise real Xcode packaging of tiny Mach-O fixtures, not the full Valhalla build."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package_apple_native as packaging
import test_verify_native_prebuilt as fixtures

P = packaging.P


class ApplePackagingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PrebuiltTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.base = self.repo / "build/apple"
        self.base.mkdir(parents=True)
        self.headers = self.base / "arm64-ios/install/include"
        self.headers.mkdir(parents=True)
        (self.headers / "fixture.h").write_text("int fixture(void);\n")
        source = self.fixture.root / "fixture.c"
        source.write_text("int fixture(void) { return 42; }\n")
        compiler = Path(subprocess.check_output(["xcrun", "--find", "clang"], text=True).strip())
        snapshot = self.fixture.root / "apple-source.json"
        snapshot.write_bytes(P.canonical(P.snapshot(self.repo, self.fixture.manifest)))
        for abi in packaging.ABIS:
            folder = self.base / abi
            folder.mkdir(exist_ok=True)
            headers = folder / "install/include"
            headers.mkdir(parents=True, exist_ok=True)
            (headers / "fixture.h").write_text("int fixture(void);\n")
            target = {"arm64-ios": "arm64-apple-ios16.4",
                      "arm64-ios-simulator": "arm64-apple-ios16.4-simulator",
                      "x64-ios-simulator": "x86_64-apple-ios16.4-simulator"}[abi]
            obj = folder / "fixture.o"
            binary = folder / packaging.LIBRARY
            subprocess.run([str(compiler), "-target", target, "-c", str(source), "-o", str(obj)],
                           check=True, capture_output=True)
            subprocess.run(["xcrun", "libtool", "-static", "-o", str(binary), str(obj)],
                           check=True, capture_output=True)
            P.emit(argparse.Namespace(source=snapshot, repo=self.repo, manifest=self.fixture.manifest,
                   require_clean=False, binary=binary, compiler=compiler, toolchain_id=target,
                   abi=abi, output=Path(str(binary) + ".provenance.json"), replace=False,
                   headers_dir=headers))
        self.environment = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # An inherited CI SHA must not override the temporary repository identity.
        self.sha = mock.patch.dict(os.environ, {"GITHUB_SHA": self.fixture.revision})
        self.sha.start()
        self.addCleanup(self.sha.stop)

    def test_real_three_architecture_package_preserves_inputs_and_headers(self):
        output = packaging.package(self.repo)
        receipt = P.load_json(output / "native-package.provenance.json")
        self.assertEqual(set(receipt["inputs"]), set(packaging.ABIS))
        self.assertEqual(receipt["headers"], packaging.tree_identity(packaging.tree(self.headers)))
        members = packaging.tree(output)
        del members["native-package.provenance.json"]
        self.assertEqual(receipt["outputs"], packaging.tree_identity(members))
        before = packaging.tree(output)
        with self.assertRaises(P.ProvenanceError):
            packaging.package(self.repo)
        self.assertEqual(packaging.tree(output), before)

    def test_corrupt_input_rejected_before_any_transform(self):
        binary = self.base / packaging.ABIS[1] / packaging.LIBRARY
        binary.write_bytes(binary.read_bytes() + b"corrupt")
        with mock.patch.object(packaging, "run") as command:
            with self.assertRaises(P.ProvenanceError):
                packaging.package(self.repo)
            command.assert_not_called()
        self.assertFalse((self.base / "valhalla-wrapper.xcframework").exists())

    def test_transform_failure_cleans_owned_stage_and_keeps_inputs(self):
        before = {abi: P.file_hash(self.base / abi / packaging.LIBRARY) for abi in packaging.ABIS}
        with mock.patch.object(packaging, "run", side_effect=subprocess.CalledProcessError(37, "lipo")):
            with self.assertRaises(subprocess.CalledProcessError):
                packaging.package(self.repo)
        self.assertFalse(list(self.base.glob(".apple-package-*")))
        for abi, digest in before.items():
            self.assertEqual(P.file_hash(self.base / abi / packaging.LIBRARY), digest)

    def test_late_empty_output_is_preserved(self):
        output = self.base / "valhalla-wrapper.xcframework"
        publish = packaging.publish_exclusive
        inode = []

        def late_output(staged, destination):
            destination.mkdir()
            inode.append(destination.stat().st_ino)
            publish(staged, destination)

        with mock.patch.object(packaging, "publish_exclusive", side_effect=late_output):
            with self.assertRaises(FileExistsError):
                packaging.package(self.repo)
        self.assertEqual(output.stat().st_ino, inode[0])
        self.assertEqual(list(output.iterdir()), [])
        self.assertFalse(list(self.base.glob(".apple-package-*")))

    def test_missing_receipt_rejected_before_any_transform(self):
        (self.base / packaging.ABIS[2] / (packaging.LIBRARY + ".provenance.json")).unlink()
        with mock.patch.object(packaging, "run") as command:
            with self.assertRaises(OSError):
                packaging.package(self.repo)
            command.assert_not_called()

    def test_changed_headers_are_rejected_before_any_transform(self):
        (self.headers / "fixture.h").write_text("int foreign_header(void);\n")
        with mock.patch.object(packaging, "run") as command:
            with self.assertRaises(P.ProvenanceError):
                packaging.package(self.repo)
            command.assert_not_called()

    def test_receipt_without_header_authority_cannot_package(self):
        path = self.base / packaging.ABIS[0] / (packaging.LIBRARY + ".provenance.json")
        receipt = P.load_json(path)
        del receipt["headers"]
        path.write_bytes(P.canonical(receipt))
        with mock.patch.object(packaging, "run") as command:
            with self.assertRaises(P.ProvenanceError):
                packaging.package(self.repo)
            command.assert_not_called()

    def test_changed_source_during_transform_cannot_publish(self):
        run = packaging.run

        def change_source(*args):
            run(*args)
            if args[0] == "xcodebuild":
                (self.repo / "tracked.txt").write_text("source changed during packaging\n")

        with mock.patch.object(packaging, "run", side_effect=change_source):
            with self.assertRaises(P.ProvenanceError):
                packaging.package(self.repo)
        self.assertFalse((self.base / "valhalla-wrapper.xcframework").exists())
        self.assertFalse(list(self.base.glob(".apple-package-*")))

    def test_changed_packaged_slice_cannot_publish(self):
        run = packaging.run

        def change_output(*args):
            run(*args)
            if args[0] == "xcodebuild":
                output = Path(args[-1])
                info = packaging.plistlib.loads((output / "Info.plist").read_bytes())
                device = next(item for item in info["AvailableLibraries"]
                              if "SupportedPlatformVariant" not in item)
                binary = output / device["LibraryIdentifier"] / device["LibraryPath"]
                binary.write_bytes(binary.read_bytes() + b"unexpected output")

        with mock.patch.object(packaging, "run", side_effect=change_output):
            with self.assertRaises((P.ProvenanceError, subprocess.SubprocessError)):
                packaging.package(self.repo)
        self.assertFalse((self.base / "valhalla-wrapper.xcframework").exists())
        self.assertFalse(list(self.base.glob(".apple-package-*")))


class PackageReceiptBoundsTests(unittest.TestCase):
    def test_realistic_two_slice_header_count_has_bounded_receipt(self):
        with tempfile.TemporaryDirectory(prefix="apple-header-size-") as work:
            root = Path(work)
            for slice_name in ("device", "simulator"):
                folder = root / slice_name
                folder.mkdir()
                for index in range(9805):
                    (folder / (str(index) + ".h")).write_bytes(b"/* fixture */\n")
            files = packaging.tree(root)
            self.assertEqual(len(files), 19610)
            identity = packaging.tree_identity(files)
            receipt = root / "receipt.json"
            receipt.write_bytes(P.canonical({"outputs": identity}))
            self.assertLess(receipt.stat().st_size, 1024)
            self.assertEqual(P.load_json(receipt)["outputs"], identity)


if __name__ == "__main__":
    unittest.main()
