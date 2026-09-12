#!/usr/bin/env python3
"""PURPOSE: Decide whether Gradle may reuse an existing native ABI library.
RESPONSIBILITY: Derive source authority independently before verifying a receipt.
DEPENDENCIES: native_artifact_provenance, Python standard library, and Git.
CONSUMERS: Gradle native preBuild tasks; 0 verified, 10 absent, 1 rejected.
"""
import argparse
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import native_artifact_provenance as provenance


def expected_prebuilt_source(repo, manifest_path, expected_revision=None):
    pins = provenance.manifest(manifest_path)
    head = provenance.git(repo, "rev-parse", "HEAD").decode().strip()
    if expected_revision is not None:
        provenance.hex_value(expected_revision, 40)
        if head != expected_revision:
            raise provenance.ProvenanceError("CI revision differs from checked-out wrapper")
    link = provenance.git(repo, "ls-tree", "HEAD", "--", provenance.CORE_PATH).decode().strip()
    index = provenance.git(repo, "ls-files", "--stage", "--", provenance.CORE_PATH).decode().strip()
    core_pin = pins["expected_core"]
    if link != f"160000 commit {core_pin}\t{provenance.CORE_PATH}" or \
            index != f"160000 {core_pin} 0\t{provenance.CORE_PATH}":
        raise provenance.ProvenanceError("Core gitlink differs from trusted manifest")
    if provenance.file_hash(repo / provenance.PATCH_PATH) != pins["patch_sha"]:
        raise provenance.ProvenanceError("Patch bytes differ from trusted manifest")
    core = repo / provenance.CORE_PATH
    if core.is_symlink():
        raise provenance.ProvenanceError("Core checkout must not be a symlink")
    if os.path.lexists(core / ".git"):
        # CI must never reuse a local dirty build, even when its receipt matches exactly.
        return provenance.source_from(provenance.snapshot(
            repo, manifest_path, require_clean=os.environ.get("GITHUB_ACTIONS") == "true"))
    # Packaging without a core checkout cannot establish a dirty source identity.
    # Its only authority is the current clean wrapper revision and committed pins.
    status = provenance.git(repo, "status", "--porcelain", "--untracked-files=all",
                            "--ignore-submodules=all")
    if status.strip():
        raise provenance.ProvenanceError("Prebuilt packaging without core requires a clean wrapper")
    if core.exists() and any(core.iterdir()):
        raise provenance.ProvenanceError("Uninitialized core contains unverified source files")
    return provenance.expected_source(head, pins)


def verify_prebuilt(repo, abi, expected_revision=None, binary=None):
    repo = Path(repo).resolve(strict=True)
    if abi not in {**provenance.ELF_ABIS, **provenance.APPLE_ABIS}:
        raise provenance.ProvenanceError("Unsupported native ABI")
    if binary is None:
        if abi not in provenance.ELF_ABIS:
            raise provenance.ProvenanceError("Apple verification requires an explicit binary")
        binary = repo / "android/valhalla/src/main/jniLibs" / abi / "libvalhalla-wrapper.so"
    binary = Path(binary)
    if not os.path.lexists(binary):
        return False
    manifest_path = repo / "patches/valhalla/manifest.cmake"
    expected = expected_prebuilt_source(repo, manifest_path, expected_revision)
    receipt = provenance.load_json(Path(str(binary) + ".provenance.json"))
    provenance.verify(binary, abi, receipt, expected)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--abi", required=True)
    parser.add_argument("--binary")
    args = parser.parse_args(argv)
    try:
        revision = os.environ.get("GITHUB_SHA")
        if os.environ.get("GITHUB_ACTIONS") == "true" and not revision:
            raise provenance.ProvenanceError("CI requires the checked-out wrapper revision")
        present = verify_prebuilt(args.repo, args.abi, revision, args.binary)
        print("Native prebuilt verified" if present else "Native binary absent; source build required")
        return 0 if present else 10
    except (provenance.ProvenanceError, OSError, ValueError) as error:
        print("Native prebuilt rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
