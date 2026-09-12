"""Transport every verified Android ABI and bind packaged AAR bytes to the build receipts."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
import verify_native_prebuilt as guard

P = guard.provenance
ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
BINARY = "libvalhalla-wrapper.so"
RECEIPT = BINARY + ".provenance.json"
JNI = Path("android/valhalla/src/main/jniLibs")


def expected_revision():
    revision = os.environ.get("GITHUB_SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not revision:
        raise P.ProvenanceError("CI requires its checked-out wrapper revision")
    return revision


def verified(repo, paths):
    receipts = {}
    for abi in ABIS:
        if not guard.verify_prebuilt(repo, abi, expected_revision(), paths[abi]):
            raise P.ProvenanceError("Required Android ABI is missing: " + abi)
        receipts[abi] = P.load_json(paths[abi].with_name(RECEIPT))
    if len({P.digest(r["source"]) for r in receipts.values()}) != 1:
        raise P.ProvenanceError("Android inputs have different source identities")
    return receipts


def scratch(repo):
    folder = Path(repo) / "build"
    folder.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=".android-transport-", dir=folder)


def publish_file(source, output):
    # Hard-link publication is atomic and refuses an existing destination.
    # Both paths must be on the same filesystem; there is no overwrite fallback.
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, output)


def combine(repo, input_root, output):
    repo, input_root = Path(repo), Path(input_root)
    paths = {a: input_root / ("libvalhalla-" + a) / BINARY for a in ABIS}
    receipts = verified(repo, paths)
    with scratch(repo) as work:
        staged = Path(work) / "jni.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for abi in ABIS:
                for name in (BINARY, RECEIPT):
                    archive.write(paths[abi].with_name(name), str(JNI / abi / name))
        if verified(repo, paths) != receipts:
            raise P.ProvenanceError("Android inputs changed during transport")
        # Verify the actual ZIP payload too, not only the files read before/after it.
        extracted = Path(work) / "check"
        extracted.mkdir()
        staged_paths = unpack(staged, extracted)
        if verified(repo, staged_paths) != receipts:
            raise P.ProvenanceError("ZIP payload differs from verified inputs")
        publish_file(staged, output)


def unpack(archive_path, folder):
    expected = {str(JNI / a / n) for a in ABIS for n in (BINARY, RECEIPT)}
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(expected) or set(names) != expected:
            raise P.ProvenanceError("JNI archive must contain exactly four libraries and receipts")
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.file_size > 8 * 1024**3:
                raise P.ProvenanceError("Invalid JNI archive member")
            if info.filename.endswith(RECEIPT) and info.file_size > P.MAX_JSON:
                raise P.ProvenanceError("JNI receipt exceeds metadata budget")
            target = folder / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
    return {a: folder / JNI / a / BINARY for a in ABIS}


def install(repo, archive):
    repo = Path(repo)
    with scratch(repo) as work:
        paths = unpack(archive, Path(work))
        verified(repo, paths)
        destinations = {a: repo / JNI / a for a in ABIS}
        if any(os.path.lexists(p) for p in destinations.values()):
            raise P.ProvenanceError("JNI destination exists; preserve it before transport installation")
        (repo / JNI).mkdir(parents=True, exist_ok=True)
        for abi, folder in destinations.items():
            folder.mkdir()  # Exclusive ownership; an interrupted pair fails later verification.
            for name in (BINARY, RECEIPT):
                os.link(paths[abi].with_name(name), folder / name)
        verified(repo, {a: p / BINARY for a, p in destinations.items()})


def match_packaged_native(repo, source, abi, artifact, size, digest, strip_tool):
    operation = "identity"
    if size != artifact["size"] or digest != artifact["sha256"]:
        if strip_tool is None:
            raise P.ProvenanceError("AAR native bytes differ from verified build")
        with scratch(repo) as work:
            stripped = Path(work) / BINARY
            P.command([str(strip_tool), "--strip-unneeded", "-o", str(stripped), str(source)])
            P.binary_abi(stripped, abi)
            if stripped.stat().st_size != size or P.file_hash(stripped) != digest:
                raise P.ProvenanceError("AAR native bytes differ from deterministic strip output")
        operation = "strip-unneeded"
    return {"operation": operation, "inputSha256": artifact["sha256"],
            "outputSha256": digest, "outputSize": size}


def strip_identity(path):
    # NDK llvm-strip may be a symlink to llvm-objcopy: argv[0] selects its operation.
    # Resolve only for hashing, never for execution or version identification.
    version = P.command([str(path), "--version"]).decode().strip()
    if not version or len(version) > 16384:
        raise P.ProvenanceError("Invalid strip tool version output")
    return {"id": "agp-8.2.2:strip-unneeded", "compilerName": path.name,
            "compilerSha256": P.file_hash(path.resolve(strict=True)), "compilerVersion": version}


def verify_aar(repo, aar, output, strip_tool=None):
    repo, aar = Path(repo), Path(aar)
    paths = {a: repo / JNI / a / BINARY for a in ABIS}
    receipts = verified(repo, paths)
    tool = None
    if strip_tool is not None:
        strip_tool = Path(strip_tool).absolute()
        tool = strip_identity(strip_tool)
    transforms = {}
    aar_hash = P.file_hash(aar)
    expected = {f"jni/{a}/{BINARY}": a for a in ABIS}
    with zipfile.ZipFile(aar) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)) or \
                {n for n in names if n.startswith("jni/") and n.endswith(".so")} != set(expected):
            raise P.ProvenanceError("AAR native members are missing, duplicated, or unexpected")
        for name, abi in expected.items():
            info = archive.getinfo(name)
            if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG):
                raise P.ProvenanceError("AAR native member must be a regular file")
            if info.file_size > 8 * 1024**3:
                raise P.ProvenanceError("AAR native member exceeds byte budget")
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            transforms[abi] = match_packaged_native(repo, paths[abi], abi,
                receipts[abi]["artifact"], info.file_size, digest.hexdigest(), strip_tool)
    if P.file_hash(aar) != aar_hash or verified(repo, paths) != receipts:
        raise P.ProvenanceError("AAR or input identity changed during verification")
    receipt = {"schemaVersion": 1, "kind": "valhalla-android-package",
               "aar": {"name": aar.name, "sha256": aar_hash, "size": aar.stat().st_size},
               "inputs": receipts}
    if tool is not None:
        if strip_identity(strip_tool) != tool:
            raise P.ProvenanceError("Strip tool changed during verification")
        receipt.update(stripTool=tool, transforms=transforms)
    with scratch(repo) as work:
        staged = Path(work) / "receipt.json"
        staged.write_bytes(P.canonical(receipt))
        publish_file(staged, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("combine", "install", "verify-aar"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--input-root")
    parser.add_argument("--archive")
    parser.add_argument("--aar")
    parser.add_argument("--output")
    parser.add_argument("--strip-tool")
    args = parser.parse_args()
    try:
        if args.action == "combine":
            combine(args.repo, args.input_root, args.output)
        elif args.action == "install":
            install(args.repo, args.archive)
        else:
            verify_aar(args.repo, args.aar, args.output, args.strip_tool)
        return 0
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as error:
        print("Android transport rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
