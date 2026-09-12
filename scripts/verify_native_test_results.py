#!/usr/bin/env python3
"""Require complete passing Android trace instrumentation results.

PURPOSE: Prevent empty, filtered, skipped, or duplicated native test runs from passing CI.
RESPONSIBILITY: Bounded XML parsing and exact selected class/method accounting.
DEPENDENCIES: Python standard library; Gradle connected-test XML reports.
CONSUMERS: Native verification workflow after its existing assertions.
"""
import argparse
import os
from pathlib import Path
import stat
import sys
from xml.parsers import expat

EXPECTED = {
    'com.valhalla.valhalla.ValhallaRawTraceRouteTest': frozenset({
        'traceUsesMapMatchingAndActorSurvivesErrors', 'closeIsIdempotentAndRejectsBothActions'}),
    'com.valhalla.valhalla.ValhallaTraceEvidenceTest': frozenset({
        'originalSamplesErrorsAndSameActorRecovery', 'boundedPrefixesAlternativesAndDiscontinuities',
        'numericTokensAreNeverRoundedOrConfusedWithStrings'}),
}
MAX_FILES = 128
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 8_388_608
MAX_ENTRIES = 4096
MAX_ELEMENTS = 20_000
MAX_DEPTH = 32


class ResultError(ValueError):
    pass


def report_files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ResultError('Expected a real results directory')
    pending, files, entries = [root], [], 0
    while pending:
        with os.scandir(pending.pop()) as children:
            for entry in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ResultError('Results directory exceeds entry limit')
                if entry.is_symlink():
                    raise ResultError('Symlinks are not native test evidence')
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.name.endswith('.xml'):
                    if not entry.is_file(follow_symlinks=False):
                        raise ResultError('XML report must be a regular file')
                    files.append(Path(entry.path))
                    if len(files) > MAX_FILES:
                        raise ResultError('Too many XML reports')
    if not files:
        raise ResultError('No XML test results found')
    return sorted(files)


def read_report(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
            raise ResultError('XML report exceeds file limit or is not regular')
        raw = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if len(raw) > MAX_FILE_BYTES or fields(before) != fields(after) or fields(after) != fields(current):
        raise ResultError('XML report changed while reading')
    return raw


class ResultsParser:
    def __init__(self, seen):
        self.seen, self.depth, self.elements, self.active = seen, 0, 0, None
        self.root = None
        self.stack = []
        self.selected_suites = set()

    def start(self, name, attributes):
        parent = self.stack[-1] if self.stack else None
        self.stack.append(name)
        self.depth += 1
        self.elements += 1
        if self.depth > MAX_DEPTH or self.elements > MAX_ELEMENTS:
            raise ResultError('XML structure exceeds limits')
        if self.depth == 1:
            self.root = name
            if name not in ('testsuite', 'testsuites'):
                raise ResultError('Unexpected XML results root')
        if name == 'testsuite' and attributes.get('name') in EXPECTED:
            self.selected_suites.add(self.depth)
            for field in ('failures', 'errors', 'skipped', 'disabled'):
                if field in attributes and attributes[field] != '0':
                    raise ResultError('Selected suite reports unsuccessful tests')
        if name in ('failure', 'error', 'skipped') and (self.active is not None or self.selected_suites):
            raise ResultError('Selected native test did not pass')
        if name != 'testcase':
            return
        if self.active is not None or parent != 'testsuite':
            raise ResultError('Testcase must be a direct suite child')
        class_name = attributes.get('classname')
        if class_name not in EXPECTED:
            return
        method = attributes.get('name')
        if method not in EXPECTED[class_name]:
            raise ResultError('Unexpected selected native test method')
        key = (class_name, method)
        if key in self.seen:
            raise ResultError('Duplicate selected native test result')
        if attributes.get('status', 'run') not in ('run', 'passed') or \
                attributes.get('result', 'completed') not in ('completed', 'passed'):
            raise ResultError('Selected native test was not executed successfully')
        self.active = (key, self.depth)

    def end(self, name):
        if name == 'testcase' and self.active is not None and self.active[1] == self.depth:
            self.seen.add(self.active[0])
            self.active = None
        self.selected_suites.discard(self.depth)
        self.stack.pop()
        self.depth -= 1

    def parse(self, raw):
        parser = expat.ParserCreate()
        parser.StartElementHandler = self.start
        parser.EndElementHandler = self.end

        def forbidden(*unused):
            raise ResultError('DTD and entity declarations are not allowed')

        parser.StartDoctypeDeclHandler = forbidden
        parser.EntityDeclHandler = forbidden
        parser.ExternalEntityRefHandler = forbidden
        try:
            parser.Parse(raw, True)
        except expat.ExpatError as error:
            raise ResultError('Malformed XML test results') from error


def verify_android(results):
    seen, total = set(), 0
    files = report_files(results)
    for path in files:
        raw = read_report(path)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ResultError('XML reports exceed total byte limit')
        ResultsParser(seen).parse(raw)
    expected = {(name, method) for name, methods in EXPECTED.items() for method in methods}
    if seen != expected:
        raise ResultError('Required native trace test results are missing')
    return {'selectedPassed': len(seen), 'xmlFiles': len(files)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='platform', required=True)
    android = commands.add_parser('android')
    android.add_argument('--results', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_android(args.results)
        print('Android native trace results verified: ' + str(result['selectedPassed']) + ' passed')
        return 0
    except (ResultError, OSError, ValueError) as error:
        print('Native test results rejected: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
