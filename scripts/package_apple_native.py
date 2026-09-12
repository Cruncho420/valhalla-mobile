"""Package verified Apple inputs and retain the exact input/output transformation evidence."""
import argparse
import ctypes
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import verify_native_prebuilt as guard

P = guard.provenance
ABIS = ("arm64-ios", "arm64-ios-simulator", "x64-ios-simulator")
LIBRARY = "libvalhalla_all.a"


def run(*args):
    subprocess.run(args, check=True, timeout=180)


def tree(root):
    if root.is_symlink() or not root.is_dir():
        raise P.ProvenanceError("Expected a real header/package directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise P.ProvenanceError("Package inputs cannot contain symlinks")
        if path.is_dir():
            continue
        if len(result) >= 50000:
            raise P.ProvenanceError("Package file count exceeds bound")
        result[str(path.relative_to(root))] = P.file_hash(path)
    if not result:
        raise P.ProvenanceError("Package tree is empty")
    return result


def tree_identity(files):
    return {"fileCount": len(files), "treeSha256": P.digest(files)}


def publish_exclusive(staged, output):
    # Darwin's RENAME_EXCL refuses even an empty destination created after preflight.
    # There is no check-then-rename fallback: that would reintroduce replacement races.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(staged), os.fsencode(output), 0x00000004) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(output))


def verify_packaged(package, inputs, headers, temporary):
    with (package / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    libraries = info.get("AvailableLibraries", [])
    if len(libraries) != 2:
        raise P.ProvenanceError("Expected device and universal simulator libraries")
    seen = set()
    for entry in libraries:
        variant = entry.get("SupportedPlatformVariant", "device")
        identifier = entry["LibraryIdentifier"]
        if variant not in ("device", "simulator") or variant in seen or \
                entry.get("SupportedPlatform") != "ios":
            raise P.ProvenanceError("Unexpected XCFramework platform")
        seen.add(variant)
        # Xcode produces these paths, but never permit an escaped verification target.
        for value in (identifier, entry["LibraryPath"], entry["HeadersPath"]):
            if Path(value).name != value or value in (".", ".."):
                raise P.ProvenanceError("Unexpected XCFramework member path")
        folder = package / identifier
        binary = folder / entry["LibraryPath"]
        if tree(folder / entry["HeadersPath"]) != headers:
            raise P.ProvenanceError("Packaged headers differ from input")
        expected_abis = ABIS[:1] if variant == "device" else ABIS[1:]
        expected_arches = {P.APPLE_ABIS[a][0] for a in expected_abis}
        if set(entry["SupportedArchitectures"]) != expected_arches:
            raise P.ProvenanceError("Unexpected XCFramework architectures")
        for abi in expected_abis:
            P.binary_abi(binary, abi)
            candidate = binary
            if variant == "simulator":
                candidate = temporary / (abi + ".a")
                run("xcrun", "lipo", str(binary), "-thin", P.APPLE_ABIS[abi][0],
                    "-output", str(candidate))
            if P.file_hash(candidate) != inputs[abi]["artifact"]["sha256"]:
                raise P.ProvenanceError("Packaged slice differs from verified input")


def package(repo):
    repo = Path(repo).resolve(strict=True)
    base = repo / "build/apple"
    output = base / "valhalla-wrapper.xcframework"
    if os.path.lexists(output):
        raise P.ProvenanceError("Output already exists; preserve it until explicitly retired")
    revision = os.environ.get("GITHUB_SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not revision:
        raise P.ProvenanceError("CI requires its checked-out wrapper revision")
    inputs = {}
    for abi in ABIS:
        binary = base / abi / LIBRARY
        if not guard.verify_prebuilt(repo, abi, revision, binary):
            raise P.ProvenanceError("Required Apple native input is missing")
        inputs[abi] = P.load_json(Path(str(binary) + ".provenance.json"))
        if inputs[abi].get("headers") != P.tree_identity(base / abi / "install/include"):
            raise P.ProvenanceError("Apple headers differ from build-time authority")
    if len({P.digest(value["source"]) for value in inputs.values()}) != 1:
        raise P.ProvenanceError("Apple inputs were built from different source identities")
    headers_dir = base / ABIS[0] / "install/include"
    headers = tree(headers_dir)
    with tempfile.TemporaryDirectory(prefix=".apple-package-", dir=base) as work:
        temporary = Path(work)
        universal = temporary / LIBRARY
        run("xcrun", "lipo", "-create", *(str(base / a / LIBRARY) for a in ABIS[1:]),
            "-output", str(universal))
        staged = temporary / output.name
        run("xcodebuild", "-create-xcframework", "-library", str(base / ABIS[0] / LIBRARY),
            "-headers", str(headers_dir), "-library", str(universal),
            "-headers", str(headers_dir), "-output", str(staged))
        verify_packaged(staged, inputs, headers, temporary)
        # Recheck original source and inputs after all transformations.
        for abi in ABIS:
            binary = base / abi / LIBRARY
            if not guard.verify_prebuilt(repo, abi, revision, binary) or \
                    P.load_json(Path(str(binary) + ".provenance.json")) != inputs[abi]:
                raise P.ProvenanceError("Apple input changed during packaging")
            if P.tree_identity(base / abi / "install/include") != inputs[abi]["headers"]:
                raise P.ProvenanceError("Apple input headers changed during packaging")
        if tree(headers_dir) != headers:
            raise P.ProvenanceError("Headers changed during packaging")
        receipt = {"schemaVersion": 1, "kind": "valhalla-apple-package",
                   "inputs": inputs, "headers": tree_identity(headers),
                   "outputs": tree_identity(tree(staged))}
        (staged / "native-package.provenance.json").write_bytes(P.canonical(receipt))
        publish_exclusive(staged, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    try:
        print(package(args.repo))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Apple packaging rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
