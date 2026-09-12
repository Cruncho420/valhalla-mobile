"""PURPOSE: Verify test APK native provenance with real Git and tiny ELF-format fixtures.
RESPONSIBILITY: Exercise exact four-ABI packaging and refusal paths before installation.
DEPENDENCIES: Standard library and existing transport fixtures; no executable JNI or device.
CONSUMERS: Android test artifact guard checks.
"""
import importlib
from pathlib import Path
import shutil
import stat
import unittest
from unittest import mock
import warnings
import zipfile
import test_android_artifact_transport as fixtures

P = fixtures.P


class AndroidTestApkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AndroidTransportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.root = self.fixture.repo, self.fixture.root
        for abi in fixtures.ABIS:
            dest = self.repo / fixtures.JNI / abi
            dest.mkdir(parents=True)
            for suffix in ('', '.provenance.json'):
                shutil.copyfile(Path(str(self.fixture.binary(abi)) + suffix),
                                dest / (fixtures.BINARY + suffix))
        self.aar = self.fixture.make_aar()
        self.apk = self.root / 'test.apk'
        self.output = self.root / 'test-apk.json'
        self.strip = self.root / 'fixture-strip'
        self.strip.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
                              ' printf "Identity fixture strip 1.0\\n"\nelse\n exit 71\nfi\n')
        self.strip.chmod(0o755)
        self.subject = importlib.import_module('verify_android_test_apk')
        self.rows = [(f'lib/{abi}/{fixtures.BINARY}', self.fixture.binary(abi).read_bytes())
                     for abi in fixtures.ABIS]
        self.write_apk(self.rows)

    def write_apk(self, rows):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.apk, 'w') as archive:
                archive.writestr('AndroidManifest.xml', b'fixture manifest')
                for name, data in rows:
                    archive.writestr(name, data)

    def verify(self, producer=None):
        self.subject.verify(self.repo, self.aar, self.apk, self.output, self.strip, producer)

    def assert_rejected(self, producer=None):
        with self.assertRaises((P.ProvenanceError, OSError, ValueError, zipfile.BadZipFile)):
            self.verify(producer)
        self.assertFalse(self.output.exists())

    def test_all_four_native_libraries_match_independent_aar_proof(self):
        self.verify()
        receipt = P.load_json(self.output)
        self.assertEqual(receipt['kind'], 'valhalla-android-test-apk')
        self.assertEqual(receipt['apk']['sha256'], P.file_hash(self.apk))
        self.assertEqual(receipt['aar']['sha256'], P.file_hash(self.aar))
        self.assertEqual(set(receipt['native']), set(fixtures.ABIS))
        for abi in fixtures.ABIS:
            self.assertEqual(receipt['native'][abi], {
                'sha256': P.file_hash(self.fixture.binary(abi)), 'size': 64})

    def test_matching_downloaded_receipt_accepted(self):
        producer = self.root / 'producer.json'
        self.fixture.transport.verify_aar(self.repo, self.aar, producer, self.strip)
        self.verify(producer)
        self.assertEqual(P.load_json(self.output)['aarVerificationSha256'], P.file_hash(producer))

    def test_wrong_same_size_native_bytes_rejected(self):
        name, data = self.rows[0]
        self.write_apk([(name, data[:-1] + b'x')] + self.rows[1:])
        self.assert_rejected()

    def test_missing_abi_rejected(self):
        self.write_apk(self.rows[:-1])
        self.assert_rejected()

    def test_duplicate_native_member_rejected(self):
        self.write_apk(self.rows + [self.rows[0]])
        self.assert_rejected()

    def test_misplaced_native_member_rejected(self):
        name, data = self.rows[0]
        self.write_apk([('assets/' + name, data)] + self.rows[1:])
        self.assert_rejected()

    def test_symlink_native_member_rejected(self):
        name, data = self.rows[0]
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write_apk([(info, data)] + self.rows[1:])
        self.assert_rejected()

    def test_different_producer_receipt_rejected(self):
        producer = self.root / 'producer.json'
        self.fixture.transport.verify_aar(self.repo, self.aar, producer, self.strip)
        receipt = P.load_json(producer)
        receipt['inputs']['x86']['source']['wrapperRevision'] = '1' * 40
        producer.write_bytes(P.canonical(receipt))
        self.assert_rejected(producer)

    def test_apk_changes_after_initial_hash_rejected(self):
        original = self.fixture.transport.verify_aar
        def mutate(*args, **kwargs):
            original(*args, **kwargs)
            with zipfile.ZipFile(self.apk, 'a') as archive:
                archive.writestr('late.txt', b'changed')
        with mock.patch.object(self.fixture.transport, 'verify_aar', mutate):
            self.assert_rejected()

    def test_aar_changes_after_independent_verification_rejected(self):
        original = self.fixture.transport.verify_aar
        def mutate(*args, **kwargs):
            original(*args, **kwargs)
            with zipfile.ZipFile(self.aar, 'a') as archive:
                archive.writestr('late.txt', b'changed')
        with mock.patch.object(self.fixture.transport, 'verify_aar', mutate):
            self.assert_rejected()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'previous owned receipt')
        with self.assertRaises(FileExistsError):
            self.verify()
        self.assertEqual(self.output.read_bytes(), b'previous owned receipt')


if __name__ == '__main__':
    unittest.main()
