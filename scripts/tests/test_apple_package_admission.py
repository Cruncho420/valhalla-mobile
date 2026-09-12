"""Verify consumer admission using real tiny Mach-O packages."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_apple_packaging_integration as fixtures
import verify_apple_package as admission


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplePackagingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo = self.fixture.repo
        self.package = admission.packaging.package(self.repo)

    def test_real_three_slices_accepted(self):
        receipt = admission.verify(self.repo, self.package)
        self.assertEqual(set(receipt['inputs']), set(admission.packaging.ABIS))

    def test_extra_file_rejected(self):
        (self.package / 'foreign').write_text('changed')
        with self.assertRaises(admission.P.ProvenanceError):
            admission.verify(self.repo, self.package)

    def test_changed_checkout_rejected(self):
        (self.repo / 'tracked.txt').write_text('changed')
        with self.assertRaises(admission.P.ProvenanceError):
            admission.verify(self.repo, self.package)

    def test_changed_receipt_source_rejected(self):
        path = self.package / admission.RECEIPT
        receipt = admission.P.load_json(path)
        receipt['inputs']['arm64-ios']['source']['wrapperRevision'] = '0' * 40
        path.write_bytes(admission.P.canonical(receipt))
        with self.assertRaises(admission.P.ProvenanceError):
            admission.verify(self.repo, self.package)

    def test_mutation_during_admission_rejected(self):
        verify = admission.verify_slices
        def mutate(*args):
            verify(*args)
            (self.package / 'late').write_text('changed')
        with mock.patch.object(admission, 'verify_slices', side_effect=mutate):
            with self.assertRaises(admission.P.ProvenanceError):
                admission.verify(self.repo, self.package)

    def test_relabelled_header_output_rejected_by_build_authority(self):
        for header in self.package.glob('*/Headers/fixture.h'):
            header.write_text('int changed(void);\n')
        path = self.package / admission.RECEIPT
        receipt = admission.P.load_json(path)
        headers = next(self.package.glob('*/Headers'))
        receipt['headers'] = admission.packaging.tree_identity(admission.packaging.tree(headers))
        files = admission.packaging.tree(self.package)
        del files[admission.RECEIPT]
        receipt['outputs'] = admission.packaging.tree_identity(files)
        path.write_bytes(admission.P.canonical(receipt))
        with self.assertRaises(admission.P.ProvenanceError):
            admission.verify(self.repo, self.package)

    def test_symlink_rejected(self):
        (self.package / 'link').symlink_to(self.package / 'Info.plist')
        with self.assertRaises(admission.P.ProvenanceError):
            admission.verify(self.repo, self.package)

    def test_ci_requires_revision(self):
        with mock.patch.dict(admission.os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': ''}):
            with self.assertRaises(admission.P.ProvenanceError):
                admission.verify(self.repo, self.package)


if __name__ == '__main__':
    unittest.main()
