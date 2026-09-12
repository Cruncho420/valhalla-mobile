"""Synthetic XML tests for result accounting only; these do not execute native tests."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_native_test_results as checker


class NativeResultsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native-result-xml-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cases = [(name, method) for name, methods in checker.EXPECTED.items() for method in sorted(methods)]

    def xml(self, cases=None, child='', attributes=''):
        cases = self.cases if cases is None else cases
        return '<testsuite>' + ''.join(
            f'<testcase classname="{name}" name="{method}" {attributes}>{child}</testcase>'
            for name, method in cases) + '</testsuite>'

    def write(self, text, name='results.xml'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def rejects(self):
        with self.assertRaises(checker.ResultError):
            checker.verify_android(self.root)

    def test_all_five_methods_pass_in_one_gradle_report(self):
        self.write(self.xml())
        self.assertEqual(checker.verify_android(self.root), {'selectedPassed': 5, 'xmlFiles': 1})

    def test_multiple_nested_gradle_reports_and_unrelated_failure(self):
        for index, case in enumerate(self.cases):
            self.write('<testsuites>' + self.xml([case]) + '</testsuites>', f'connected/device/{index}.xml')
        self.write('<testsuite><testcase classname="Other" name="other"><failure/></testcase></testsuite>', 'other.xml')
        self.assertEqual(checker.verify_android(self.root)['selectedPassed'], 5)

    def test_missing_method_rejected(self):
        self.write(self.xml(self.cases[:-1]))
        self.rejects()

    def test_duplicate_in_one_or_multiple_reports_rejected(self):
        for other_file in (False, True):
            with self.subTest(other_file=other_file):
                path = self.write(self.xml(self.cases if other_file else self.cases + [self.cases[0]]))
                if other_file:
                    self.write(self.xml([self.cases[0]]), 'duplicate.xml')
                self.rejects()
                path.unlink()

    def test_unexpected_method_in_selected_class_rejected(self):
        self.write(self.xml(self.cases + [(self.cases[0][0], 'unexpectedNewMethod')]))
        self.rejects()

    def test_skipped_failure_and_error_rejected(self):
        for tag in ('skipped', 'failure', 'error'):
            with self.subTest(tag=tag):
                self.write(self.xml(child=f'<{tag}>detail</{tag}>'))
                self.rejects()

    def test_notrun_or_disabled_attribute_rejected(self):
        for attributes in ('status="notrun"', 'result="suppressed"', 'status="skipped"'):
            with self.subTest(attributes=attributes):
                self.write(self.xml(attributes=attributes))
                self.rejects()

    def test_selected_suite_summary_failure_is_not_ignored(self):
        name = self.cases[0][0]
        self.write(self.xml().replace('<testsuite>', f'<testsuite name="{name}" failures="1">'))
        self.rejects()

    def test_empty_directory_or_report_rejected(self):
        self.rejects()
        for text in ('', '<testsuite/>', '<testsuites/>'):
            with self.subTest(text=text):
                self.write(text)
                self.rejects()

    def test_malformed_or_wrong_root_rejected(self):
        for text in ('<testsuite>', '<notresults/>', self.xml() + '<extra/>'):
            with self.subTest(text=text):
                self.write(text)
                self.rejects()

    def test_dtd_entities_are_rejected_without_expansion(self):
        self.write('<!DOCTYPE testsuite [<!ENTITY example "expanded">]>' + self.xml())
        self.rejects()

    def test_symlink_report_and_directory_rejected(self):
        target = self.write(self.xml(), 'source.data')
        (self.root / 'linked.xml').symlink_to(target)
        self.rejects()
        (self.root / 'linked.xml').unlink()
        (self.root / 'linked-dir').symlink_to(self.root, target_is_directory=True)
        self.rejects()

    def test_byte_element_depth_and_file_limits(self):
        self.write(self.xml())
        for constant, limit in [('MAX_FILE_BYTES', 20), ('MAX_TOTAL_BYTES', 20),
                                ('MAX_ELEMENTS', 2), ('MAX_DEPTH', 1), ('MAX_FILES', 0), ('MAX_ENTRIES', 0)]:
            with self.subTest(constant=constant), mock.patch.object(checker, constant, limit):
                self.rejects()

    def test_cli_pass_and_failure_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checker.main(['android', '--results', str(self.root)]), 1)
            self.write(self.xml())
            self.assertEqual(checker.main(['android', '--results', str(self.root)]), 0)


    def test_selected_suite_failure_outside_testcase_rejected(self):
        name = self.cases[0][0]
        self.write(self.xml().replace('<testsuite>', f'<testsuite name="{name}">')
                   .replace('</testsuite>', '<error/></testsuite>'))
        self.rejects()

    def test_selected_testcase_inside_unrelated_testcase_rejected(self):
        self.write('<testsuite><testcase classname="Other" name="wrapper">' +
                   self.xml().removeprefix('<testsuite>').removesuffix('</testsuite>') +
                   '</testcase></testsuite>')
        self.rejects()


if __name__ == '__main__':
    unittest.main(verbosity=2)
