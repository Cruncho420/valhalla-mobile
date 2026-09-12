#!/usr/bin/env python3
"""PURPOSE: Admit a downloaded XCFramework before native tests.
RESPONSIBILITY: Bind packaged slices and headers to current checkout authority.
DEPENDENCIES: Existing packaging/provenance utilities, Xcode, and Python.
CONSUMERS: iOS native trace CI.
"""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import package_apple_native as packaging

P = packaging.P
RECEIPT = "native-package.provenance.json"


def verify_slices(package, receipt, expected, temporary):
    info = plistlib.loads((package / "Info.plist").read_bytes())
    device = next(e for e in info["AvailableLibraries"]
                  if e.get("SupportedPlatformVariant", "device") == "device")
    # Paths are checked by verify_packaged before they are used below.
    headers = package / device["LibraryIdentifier"] / device["HeadersPath"]
    for abi in packaging.ABIS:
        folder = temporary / abi
        folder.mkdir()
        binary = folder / packaging.LIBRARY
        source = (package / device["LibraryIdentifier"] / device["LibraryPath"]
                  if abi == packaging.ABIS[0] else temporary / (abi + ".a"))
        shutil.copyfile(source, binary)
        P.verify(binary, abi, receipt["inputs"][abi], expected,
                 headers_dir=headers if abi == packaging.ABIS[0] else None)


def verify(repo, package):
    repo, package = Path(repo).resolve(strict=True), Path(package)
    revision = os.environ.get("GITHUB_SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not revision:
        raise P.ProvenanceError("CI requires its checked-out wrapper revision")
    manifest = repo / "patches/valhalla/manifest.cmake"
    expected = packaging.guard.expected_prebuilt_source(repo, manifest, revision)
    before = packaging.tree(package)
    receipt = P.load_json(package / RECEIPT)
    if set(receipt) != {"schemaVersion", "kind", "inputs", "headers", "outputs"} or \
            type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1 or \
            receipt["kind"] != "valhalla-apple-package" or \
            set(receipt["inputs"]) != set(packaging.ABIS):
        raise P.ProvenanceError("Invalid Apple package receipt")
    outputs = {key: value for key, value in before.items() if key != RECEIPT}
    if packaging.tree_identity(outputs) != receipt["outputs"]:
        raise P.ProvenanceError("Downloaded framework differs from packaged output")
    # Obtain headers using only paths validated by the existing packaging verifier.
    header_files = [p for p in package.glob("*/Headers") if p.is_dir()]
    if len(header_files) != 2:
        raise P.ProvenanceError("Expected two packaged header directories")
    headers = packaging.tree(header_files[0])
    if packaging.tree_identity(headers) != receipt["headers"]:
        raise P.ProvenanceError("Packaged header identity differs")
    with tempfile.TemporaryDirectory(prefix=".apple-admission-", dir=repo / "build") as work:
        temporary = Path(work)
        packaging.verify_packaged(package, receipt["inputs"], headers, temporary)
        verify_slices(package, receipt, expected, temporary)
    if packaging.tree(package) != before or \
            packaging.guard.expected_prebuilt_source(repo, manifest, revision) != expected:
        raise P.ProvenanceError("Framework or source changed during admission")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args()
    try:
        verify(args.repo, args.package)
        print("Apple framework verified against checkout authority")
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration,
            subprocess.SubprocessError) as error:
        print("Apple framework rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
