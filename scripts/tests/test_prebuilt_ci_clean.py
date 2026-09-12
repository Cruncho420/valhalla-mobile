"""PURPOSE: Reject dirty native source reuse in CI without disabling local warm caches.
RESPONSIBILITY: Exercise initialized-core CI source and revision authority.
DEPENDENCIES: Existing real Git fixture and standard library.
CONSUMERS: Native prebuilt guard regression checks.
"""
import contextlib
import io
import os
import unittest
from unittest import mock
import test_verify_native_prebuilt as fixtures


class CiCleanTests(unittest.TestCase):
    setUp = fixtures.PrebuiltTests.setUp
    git = fixtures.PrebuiltTests.git
    init = fixtures.PrebuiltTests.init
    emit = fixtures.PrebuiltTests.emit

    def test_matching_dirty_receipt_rejected_in_ci_but_valid_locally(self):
        (self.repo / 'tracked.txt').write_text('exact local edit\n')
        self.emit()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fixtures.guard.verify_prebuilt(self.repo, 'arm64-v8a'))
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
                                         'GITHUB_SHA': self.revision}, clear=True):
            with self.assertRaises(fixtures.P.ProvenanceError):
                fixtures.guard.verify_prebuilt(self.repo, 'arm64-v8a', self.revision)

    def test_ci_cli_clean_success_and_missing_or_wrong_revision_rejected(self):
        args = ['--repo', str(self.repo), '--abi', 'arm64-v8a']
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for revision, result in ((self.revision, 0), ('1' * 40, 1), (None, 1)):
                env = {'GITHUB_ACTIONS': 'true'}
                if revision is not None:
                    env['GITHUB_SHA'] = revision
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(fixtures.guard.main(args), result)

    def test_ci_cli_matching_dirty_receipt_rejected(self):
        (self.repo / 'tracked.txt').write_text('exact local edit\n')
        self.emit()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
                                         'GITHUB_SHA': self.revision}, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fixtures.guard.main(['--repo', str(self.repo), '--abi', 'arm64-v8a']), 1)


if __name__ == '__main__':
    unittest.main()
