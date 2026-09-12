"""PURPOSE: Bind installed header bytes to native build receipts.
RESPONSIBILITY: Exercise compact tree identity, tampering, and warm archive admission.
DEPENDENCIES: Standard library and isolated provenance fixture.
CONSUMERS: Native build and package validation.
"""
import os
import unittest
from unittest.mock import patch
import test_native_artifact_provenance as fixtures

P = fixtures.P


class HeaderProvenanceTests(unittest.TestCase):
    setUp = fixtures.ProvenanceTests.setUp
    git = fixtures.ProvenanceTests.git
    init = fixtures.ProvenanceTests.init
    elf = fixtures.ProvenanceTests.elf

    def headers(self):
        path = self.root / 'headers'
        path.mkdir()
        (path / 'wrapper.h').write_text('header bytes')
        return path

    def test_build_binds_tree_and_verifier_rejects_tamper_missing_renamed(self):
        headers = self.headers()
        self.args.headers_dir = headers
        receipt = P.emit(self.args)
        expected = P.source_from(P.load_json(self.source))
        self.assertEqual(receipt['headers'], P.tree_identity(headers))
        self.assertEqual(P.verify(self.binary, self.args.abi, receipt, expected,
                                 headers_dir=headers), receipt)
        (headers / 'wrapper.h').rename(headers / 'other.h')
        with self.assertRaises(P.ProvenanceError):
            P.verify(self.binary, self.args.abi, receipt, expected, headers_dir=headers)
        (headers / 'other.h').rename(headers / 'wrapper.h')
        (headers / 'wrapper.h').write_text('different bytes')
        with self.assertRaises(P.ProvenanceError):
            P.verify(self.binary, self.args.abi, receipt, expected, headers_dir=headers)
        del receipt['headers']
        with self.assertRaises(P.ProvenanceError):
            P.verify(self.binary, self.args.abi, receipt, expected, headers_dir=headers)

    def test_warm_archive_cannot_gain_headers_authority(self):
        P.emit(self.args)
        before = self.receipt.read_bytes()
        os.utime(self.binary, ns=(1, 1))
        self.args.headers_dir = self.headers()
        with self.assertRaises(P.ProvenanceError):
            P.emit(self.args)
        self.assertEqual(self.receipt.read_bytes(), before)

    def test_warm_bound_archive_accepts_only_same_headers(self):
        self.args.headers_dir = self.headers()
        receipt = P.emit(self.args)
        os.utime(self.binary, ns=(1, 1))
        self.assertEqual(P.emit(self.args), receipt)
        (self.args.headers_dir / 'wrapper.h').write_text('modified')
        with self.assertRaises(P.ProvenanceError):
            P.emit(self.args)

    def test_compact_9805_file_tree_matches_canonical_map(self):
        headers = self.headers()
        expected = {'wrapper.h': P.file_hash(headers / 'wrapper.h')}
        for index in range(9804):
            name = 'h%05d.h' % index
            (headers / name).write_bytes(b'header')
            expected[name] = P.hashlib.sha256(b'header').hexdigest()
        identity = P.tree_identity(headers)
        self.assertEqual(identity, {'fileCount': 9805, 'treeSha256': P.digest(expected)})
        self.assertLess(len(P.canonical(identity)), 128)

    def test_empty_symlink_special_and_mutating_directory_refused(self):
        empty = self.root / 'empty'; empty.mkdir()
        with self.assertRaises(P.ProvenanceError):
            P.tree_identity(empty)
        headers = self.headers()
        alias = self.root / 'alias'; alias.symlink_to(headers, target_is_directory=True)
        with self.assertRaises(P.ProvenanceError):
            P.tree_identity(alias)
        (headers / 'link').symlink_to(headers / 'wrapper.h')
        with self.assertRaises(P.ProvenanceError):
            P.tree_identity(headers)
        (headers / 'link').unlink()
        original = P.file_hash
        def mutate(path):
            result = original(path)
            (headers / 'late.h').write_text('late')
            return result
        with patch.object(P, 'file_hash', mutate), self.assertRaises(P.ProvenanceError):
            P.tree_identity(headers)

    def test_malformed_header_schema_refused(self):
        receipt = P.emit(self.args)
        expected = P.source_from(P.load_json(self.source))
        for value in ([], {}, {'fileCount': True, 'treeSha256': '0' * 64},
                      {'fileCount': 1, 'treeSha256': 'bad'}):
            receipt['headers'] = value
            with self.assertRaises(P.ProvenanceError):
                P.verify(self.binary, self.args.abi, receipt, expected)


if __name__ == '__main__':
    unittest.main()
