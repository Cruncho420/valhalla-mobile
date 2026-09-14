#!/usr/bin/env python3
"""Bind raw capture to this workflow's independently verified native package and test source."""
import argparse
from pathlib import Path
import subprocess
import os
from prospective_trace_capture import ROOT, require, read, decode, sha, encoded
import native_artifact_provenance as P


def bind(platform, capture, native_receipt, package_receipt, executable, output):
    require(not output.exists())
    receipt = decode(read(capture / 'receipt.json', 262144))
    require(receipt['platform'] == platform and receipt['complete'] is True)
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    require(revision == os.environ['GITHUB_SHA'])
    require(os.environ['GITHUB_RUN_ID'].isdigit() and os.environ['GITHUB_RUN_ATTEMPT'].isdigit())
    expected = P.expected_source(revision, P.manifest(ROOT / 'patches/valhalla/manifest.cmake'))
    native_bytes = read(native_receipt, 16 * 1048576)
    native = decode(native_bytes)
    package_bytes = native_bytes if platform == 'ios' else read(package_receipt, 16 * 1048576)
    package = decode(package_bytes)
    require(package['kind'] == ('valhalla-apple-package' if platform == 'ios' else 'valhalla-android-package'))
    require(set(package['inputs']) == ({'arm64-ios', 'arm64-ios-simulator', 'x64-ios-simulator'}
                                      if platform == 'ios' else {'arm64-v8a', 'armeabi-v7a', 'x86', 'x86_64'}))
    require(all(value['source'] == expected for value in package['inputs'].values()))
    if platform == 'android':
        require(native['kind'] == 'valhalla-android-test-apk')
        require(native['aar'] == package['aar'])
        require(receipt['nativeLibrarySha256'] == native['native'][receipt['abi']]['sha256'])
        executed = {'librarySha256': receipt['nativeLibrarySha256'], 'apk': native['apk']}
        collector = ROOT / 'android/valhalla/src/androidTest/java/com/valhalla/valhalla/ValhallaProspectiveTraceCaptureTest.kt'
    else:
        require(executable is not None and not executable.is_symlink() and executable.is_file())
        require(executable.stat().st_size <= 256 * 1048576)
        require(P.file_hash(executable) == receipt['executableSha256'])
        require(executable.stat().st_size == receipt['executableBytes'])
        arch = 'arm64' if receipt['abi'] == 'arm64-ios-simulator' else 'x86_64'
        arches = subprocess.check_output(['lipo', '-archs', str(executable)], text=True, timeout=30).split()
        require(arch in arches)
        executed = {'executableSha256': receipt['executableSha256'], 'executableBytes': receipt['executableBytes']}
        collector = ROOT / 'apple/Tests/ValhallaTests/TestProspectiveTraceCapture.swift'
    result = dict(version=1, source=expected, workflowRunId=os.environ['GITHUB_RUN_ID'],
                  workflowRunAttempt=os.environ['GITHUB_RUN_ATTEMPT'], platform=platform, abi=receipt['abi'],
                  nativeReceiptSha256=sha(native_bytes), nativePackageSource=expected, nativePackageReceiptSha256=sha(package_bytes),
                  executed=executed, collectorSourceSha256=sha(read(collector, 65536)),
                  captureReceiptSha256=sha(read(capture / 'receipt.json', 262144)),
                  graphExtractSha256=receipt['extractSha256'], configSha256=receipt['configSha256'],
                  stageSha256=receipt['stageSha256'], matcherCorrectnessAdmitted=False,
                  consumerPreservationAdmitted=False, productionReadinessAdmitted=False)
    with output.open('xb') as file:
        file.write(encoded(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', choices=['android', 'ios'], required=True)
    for name in ('capture', 'native-receipt', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--package-receipt', type=Path)
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    try:
        bind(args.platform, args.capture, args.native_receipt, args.package_receipt, args.executable, args.output)
    except Exception:
        raise SystemExit('Prospective capture provenance incomplete or invalid') from None
