"""Verify explicit NDK stripping with real tiny shared ELF libraries, not AGP or Valhalla.

The original Android transport assertions remain unchanged in their existing module.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_android_artifact_transport as fixtures

P = fixtures.P
NDK_BIN = Path('/Users/Tadas/Library/Android/sdk/ndk/27.1.12297006/toolchains/llvm/prebuilt/darwin-x86_64/bin')
TARGETS = {'arm64-v8a': 'aarch64-linux-android26', 'armeabi-v7a': 'armv7a-linux-androideabi26',
           'x86_64': 'x86_64-linux-android26', 'x86': 'i686-linux-android26'}


@unittest.skipUnless((NDK_BIN / 'clang').is_file() and (NDK_BIN / 'llvm-strip').is_file(),
                     'Explicit cached NDK compiler and strip tool are required')
class AndroidStripTransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AndroidTransportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.root = self.fixture.repo, self.fixture.root
        self.transport = self.fixture.transport
        self.strip = NDK_BIN / 'llvm-strip'
        source_code = self.root / 'fixture.c'
        source_code.write_text('int fixture_value(int x) { return x + 42; }\n')
        self.stripped = {}
        for abi, target in TARGETS.items():
            binary = self.fixture.binary(abi)
            source = self.root / (abi + '-real-source.json')
            source.write_bytes(P.canonical(P.snapshot(self.repo, self.fixture.fixture.manifest)))
            subprocess.run([str(NDK_BIN / 'clang'), '--target=' + target, '-shared', '-g',
                            '-fPIC', str(source_code), '-o', str(binary)],
                           check=True, capture_output=True)
            P.emit(argparse.Namespace(source=source, repo=self.repo,
                   manifest=self.fixture.fixture.manifest, require_clean=False, binary=binary,
                   compiler=NDK_BIN / 'clang', toolchain_id='fixture-ndk27.1-' + target,
                   abi=abi, output=Path(str(binary) + '.provenance.json'), replace=True))
            stripped = self.root / (abi + '-stripped.so')
            subprocess.run([str(self.strip), '--strip-unneeded', '-o', str(stripped), str(binary)],
                           check=True, capture_output=True)
            self.assertNotEqual(P.file_hash(stripped), P.file_hash(binary))
            self.stripped[abi] = stripped
        self.transport.install(self.repo, self.fixture.combine())

    def aar(self, stripped=True, wrong=False, mixed=False):
        output = self.root / 'strip-fixture.aar'
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'fixture, not a built Android application')
            for abi in TARGETS:
                use_stripped = stripped and not (mixed and abi == 'x86')
                binary = self.stripped[abi] if use_stripped else self.fixture.binary(abi)
                raw = binary.read_bytes()
                if wrong and abi == 'x86':
                    raw += b'foreign bytes'
                archive.writestr('jni/' + abi + '/' + fixtures.BINARY, raw)
        return output

    def output(self):
        return self.root / 'strip-package.json'

    def test_real_ndk_transform_matches_all_four_abis_and_binds_tool(self):
        aar = self.aar()
        self.transport.verify_aar(self.repo, aar, self.output(), strip_tool=self.strip)
        receipt = P.load_json(self.output())
        self.assertEqual(receipt['aar']['sha256'], P.file_hash(aar))
        self.assertEqual(set(receipt['transforms']), set(TARGETS))
        for abi in TARGETS:
            self.assertEqual(receipt['transforms'][abi], {
                'operation': 'strip-unneeded', 'inputSha256': P.file_hash(self.fixture.binary(abi)),
                'outputSha256': P.file_hash(self.stripped[abi]),
                'outputSize': self.stripped[abi].stat().st_size})
            self.assertEqual(receipt['inputs'][abi], P.load_json(
                Path(str(self.fixture.binary(abi)) + '.provenance.json')))
        self.assertIn(P.file_hash(self.strip.resolve(strict=True)), P.canonical(receipt['stripTool']).decode())

    def test_explicit_tool_preserves_identity_members_without_transform(self):
        self.transport.verify_aar(self.repo, self.aar(mixed=True), self.output(), strip_tool=self.strip)
        receipt = P.load_json(self.output())
        for abi in TARGETS:
            operation = 'identity' if abi == 'x86' else 'strip-unneeded'
            self.assertEqual(receipt['transforms'][abi]['operation'], operation)
        self.assertEqual(receipt['transforms']['x86']['inputSha256'],
                         receipt['transforms']['x86']['outputSha256'])

    def test_stripped_bytes_without_explicit_tool_are_rejected(self):
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, self.aar(), self.output())
        self.assertFalse(self.output().exists())

    def test_wrong_transformed_bytes_are_rejected(self):
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, self.aar(wrong=True), self.output(), strip_tool=self.strip)
        self.assertFalse(self.output().exists())

    def script(self, body):
        tool = self.root / 'fixture-strip'
        tool.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
                        '  printf "Fixture strip 1.0\\n"\n  exit 0\nfi\n' + body)
        tool.chmod(0o755)
        return tool

    def test_failed_strip_command_cannot_publish(self):
        tool = self.script('exit 37\n')
        with self.assertRaises((P.ProvenanceError, subprocess.SubprocessError, OSError)):
            self.transport.verify_aar(self.repo, self.aar(), self.output(), strip_tool=tool)
        self.assertFalse(self.output().exists())

    def test_success_exit_with_wrong_tool_output_cannot_publish(self):
        tool = self.script('cp "$4" "$3"\n')
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, self.aar(), self.output(), strip_tool=tool)
        self.assertFalse(self.output().exists())

    def test_tool_modifying_verified_input_cannot_publish(self):
        # Exact argv matches the contract: --strip-unneeded -o OUTPUT INPUT.
        tool = self.script('"' + str(self.strip) + '" "$@" || exit $?\nprintf changed >> "$4"\n')
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, self.aar(), self.output(), strip_tool=tool)
        self.assertFalse(self.output().exists())


    def test_tool_changing_its_own_bytes_cannot_publish(self):
        tool = self.script('"' + str(self.strip) + '" "$@" || exit $?\nprintf "# changed\\n" >> "$0"\n')
        with self.assertRaises(P.ProvenanceError):
            self.transport.verify_aar(self.repo, self.aar(), self.output(), strip_tool=tool)
        self.assertFalse(self.output().exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
