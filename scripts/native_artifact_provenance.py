#!/usr/bin/env python3
"""Native artifact receipts, checked against caller-supplied source expectations.

PURPOSE: Reject stale, mixed-source, or incorrectly labelled native artifacts.
RESPONSIBILITY: Snapshot build inputs, attest completed outputs, and verify packaging inputs.
DEPENDENCIES: Python standard library, git, and Apple lipo/otool for Apple artifacts.
CONSUMERS: Native build scripts and packaging jobs; receipts are not cryptographic signatures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import sys
import tempfile
import time

MAX_JSON = 65536
MAX_COMMAND = 8 * 1024 * 1024
COMMAND_SECONDS = 60
PATCH_PATH = "patches/valhalla/0001-meili-stateful-terminal-segment.patch"
CORE_PATH = "src/valhalla"
TARGET_PATH = "src/meili/match_route.cc"
ELF_ABIS = {"arm64-v8a": (2, 183), "armeabi-v7a": (1, 40),
            "x86": (1, 3), "x86_64": (2, 62)}
APPLE_ABIS = {"arm64-ios": ("arm64", 2), "arm64-ios-simulator": ("arm64", 7),
              "x64-ios-simulator": ("x86_64", 7), "macos": ("arm64", 1)}


class ProvenanceError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 8 * 1024**3:
            raise ProvenanceError("Expected a bounded regular file")
        sha = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ino) or (
            current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
        raise ProvenanceError("File changed while hashing")
    return sha.hexdigest()


def tree_identity(path):
    """Bind installed header names and bytes without putting thousands of paths in receipts."""
    root = Path(path)
    pending, directories, files, checked_files = [root], [], {}, []
    while pending:
        directory = pending.pop()
        before = directory.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise ProvenanceError("Header tree must contain real directories, not symlinks")
        directories.append((directory, before))
        with os.scandir(directory) as entries:
            for entry in entries:
                child = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)
                elif entry.is_file(follow_symlinks=False):
                    file_before = child.lstat()
                    files[child.relative_to(root).as_posix()] = file_hash(child)
                    checked_files.append((child, file_before))
                else:
                    raise ProvenanceError("Header tree contains a symlink or special file")
                if len(files) + len(pending) + len(directories) > 32768:
                    raise ProvenanceError("Header tree exceeds entry budget")
    for directory, before in directories + checked_files:
        after = directory.lstat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
            raise ProvenanceError("Header tree changed while hashing")
    if not files:
        raise ProvenanceError("Header tree must not be empty")
    return {"fileCount": len(files), "treeSha256": digest(files)}


def read_bounded(path, limit):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ProvenanceError("Metadata file exceeds provenance budget")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or before.st_mtime_ns != after.st_mtime_ns or \
            before.st_size != after.st_size:
        raise ProvenanceError("Metadata changed while reading")
    return data


def command(args, cwd=None):
    process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    deadline = time.monotonic() + COMMAND_SECONDS
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProvenanceError("Command exceeded provenance time budget")
                for key, _ in selector.select(min(remaining, 1)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    output.extend(block)
                    if len(output) > MAX_COMMAND:
                        raise ProvenanceError("Command output exceeds provenance budget")
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if process.returncode:
            raise ProvenanceError("Command failed: " + args[0] + ": " +
                                  output[:2048].decode(errors="replace"))
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def git(repo, *args):
    return command(["git", "-C", str(repo), *args])


def hex_value(value, length):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{" + str(length) + "}", value):
        raise ProvenanceError("Invalid source digest or revision")
    return value


def manifest(path):
    data = read_bounded(path, 4096)
    values = {}
    for line in data.decode("ascii").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r'set\((expected_core|original_sha|patched_sha|patch_sha) "([0-9a-f]+)"\)', line)
        if not match or match[1] in values:
            raise ProvenanceError("Invalid or duplicate patch manifest entry")
        values[match[1]] = hex_value(match[2], 40 if match[1] == "expected_core" else 64)
    if set(values) != {"expected_core", "original_sha", "patched_sha", "patch_sha"}:
        raise ProvenanceError("Incomplete patch manifest")
    return values


def expected_source(revision, pins, dirty=None):
    result = {"wrapperRevision": hex_value(revision, 40), "coreRevision": pins["expected_core"],
              "patchSha256": pins["patch_sha"], "patchedSourceSha256": pins["patched_sha"],
              "originalSourceSha256": pins["original_sha"], "dirtySha256": dirty,
              "clean": dirty is None}
    result["buildSourceIdentity"] = digest(result)
    return result


def dirty_entries(repo, exempt, prefix="", depth=0):
    if depth > 8:
        raise ProvenanceError("Submodule nesting exceeds budget")
    if git(repo, "rev-parse", "--show-toplevel").decode().strip() != str(Path(repo).resolve()):
        raise ProvenanceError("Required source submodule is missing")
    entries = []
    staged = git(repo, "diff", "--cached", "--binary", "--no-ext-diff")
    if staged:
        entries.append({"path": prefix + "@git-index", "stagedSha256": hashlib.sha256(staged).hexdigest()})
    stage = git(repo, "ls-files", "--stage", "-z").split(b"\0")
    links = {}
    for item in filter(None, stage):
        metadata, name = item.split(b"\t", 1)
        if metadata.startswith(b"160000 "):
            links[os.fsdecode(name)] = metadata.split()[1].decode()
    changed = git(repo, "diff", "--name-only", "--no-renames", "--no-ext-diff", "-z", "HEAD",
                  "--ignore-submodules=dirty")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    names = sorted(set(filter(None, (changed + untracked).split(b"\0"))))
    if len(names) > 8192:
        raise ProvenanceError("Dirty source file count exceeds budget")
    for encoded in names:
        name = os.fsdecode(encoded)
        if name in links:
            continue
        path = Path(repo) / name
        if path == exempt:
            continue  # The required patched contents are independently verified below.
        mode = path.lstat().st_mode if path.exists() or path.is_symlink() else None
        entries.append({"path": prefix + name, "mode": mode,
                        "sha256": file_hash(path) if mode is not None else None})
    for name, recorded in sorted(links.items()):
        subrepo = Path(repo) / name
        actual = git(subrepo, "rev-parse", "HEAD").decode().strip()
        if actual != recorded:
            entries.append({"path": prefix + name, "revision": actual})
        entries.extend(dirty_entries(subrepo, exempt, prefix + name + "/", depth + 1))
    return entries


def core_stage_and_mode(core, target):
    entries = []
    tracked = git(core, "ls-tree", "HEAD", TARGET_PATH).split()
    if not tracked or tracked[0] not in (b"100644", b"100755"):
        raise ProvenanceError("Patched target must be a tracked regular core source file")
    mode = tracked[0]
    if bool(target.stat().st_mode & 0o111) != (mode == b"100755"):
        entries.append({"path": CORE_PATH + "/" + TARGET_PATH, "executableModeChanged": True})
    return entries


def snapshot(repo, manifest_path, require_clean=False):
    repo = Path(repo).resolve()
    pins = manifest(manifest_path)
    revision = git(repo, "rev-parse", "HEAD").decode().strip()
    core = repo / CORE_PATH
    if git(core, "rev-parse", "--show-toplevel").decode().strip() != str(core):
        raise ProvenanceError("Core checkout is missing")
    if git(core, "rev-parse", "HEAD").decode().strip() != pins["expected_core"]:
        raise ProvenanceError("Core revision does not match trusted manifest")
    if file_hash(repo / PATCH_PATH) != pins["patch_sha"]:
        raise ProvenanceError("Patch bytes do not match trusted manifest")
    target = core / TARGET_PATH
    if file_hash(target) != pins["patched_sha"]:
        raise ProvenanceError("Core source is not the required patched source")
    entries = dirty_entries(repo, target) + core_stage_and_mode(core, target)
    source = expected_source(revision, pins, digest(entries) if entries else None)
    if require_clean and not source["clean"]:
        raise ProvenanceError("Clean source required; local changes are present")
    return {"schemaVersion": 1, "kind": "valhalla-build-source", "source": source}


def load_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_JSON:
        raise ProvenanceError("Invalid receipt file or size")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProvenanceError("Duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(read_bounded(path, MAX_JSON), object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ProvenanceError("Receipt must be a JSON object")
    return result


def source_from(document):
    source = document.get("source")
    if not isinstance(source, dict):
        raise ProvenanceError("Missing source identity")
    pins = {"expected_core": hex_value(source.get("coreRevision"), 40),
            "patch_sha": hex_value(source.get("patchSha256"), 64),
            "patched_sha": hex_value(source.get("patchedSourceSha256"), 64),
            "original_sha": hex_value(source.get("originalSourceSha256"), 64)}
    dirty = source.get("dirtySha256")
    if dirty is not None:
        hex_value(dirty, 64)
    if type(source.get("clean")) is not bool or source != expected_source(
            source.get("wrapperRevision"), pins, dirty):
        raise ProvenanceError("Inconsistent source identity")
    return source


def load_source(path):
    value = load_json(path)
    if set(value) != {"schemaVersion", "kind", "source"} or \
            type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or \
            value["kind"] != "valhalla-build-source":
        raise ProvenanceError("Trusted build-source snapshot required, not an artifact receipt")
    source_from(value)
    return value


def binary_abi(binary, abi):
    if abi in ELF_ABIS:
        with open(binary, "rb") as stream:
            header = stream.read(64)
        if len(header) < 52 or header[:4] != b"\x7fELF" or header[5:7] != b"\x01\x01":
            raise ProvenanceError("Expected a little-endian ELF binary")
        observed = (header[4], struct.unpack_from("<H", header, 18)[0])
        if observed != ELF_ABIS[abi] or struct.unpack_from("<H", header, 16)[0] != 3:
            raise ProvenanceError("ELF ABI or shared-library type mismatch")
        return
    if abi not in APPLE_ABIS:
        raise ProvenanceError("Unsupported ABI")
    arch, platform = APPLE_ABIS[abi]
    arches = command(["xcrun", "lipo", "-archs", str(binary)]).decode().split()
    if arch not in arches:
        raise ProvenanceError("Mach-O architecture mismatch")
    loads = command(["xcrun", "otool", "-l", "-arch", arch, str(binary)]).decode()
    platforms = {int(x) for x in re.findall(r"^\s*platform\s+(\d+)\s*$", loads, re.M)}
    if platforms != {platform}:
        raise ProvenanceError("Mach-O platform is missing, mixed, or incorrect")


def toolchain(compiler, identity):
    compiler = Path(compiler).resolve(strict=True)
    if not identity or len(identity) > 1024:
        raise ProvenanceError("Explicit bounded toolchain identity required")
    version = command([str(compiler), "--version"]).decode().strip()
    if not version or len(version) > 16384:
        raise ProvenanceError("Invalid compiler version output")
    return {"id": identity, "compilerName": compiler.name,
            "compilerSha256": file_hash(compiler), "compilerVersion": version}


def build_toolchain(args):
    cache = getattr(args, "cmake_cache", None)
    if not cache:
        return toolchain(args.compiler, args.toolchain_id)
    path = Path(cache)
    data = read_bounded(path, 1024 * 1024)
    lines = data.decode().splitlines()
    compilers = [match[1] for line in lines if (match := re.fullmatch(
        r"CMAKE_CXX_COMPILER:(?:FILEPATH|STRING)=(.+)", line))]
    if len(compilers) != 1 or not Path(compilers[0]).is_absolute():
        raise ProvenanceError("CMake cache must identify one absolute C++ compiler")
    cache_hash = hashlib.sha256(data).hexdigest()
    if file_hash(path) != cache_hash:
        raise ProvenanceError("CMake cache changed while reading compiler identity")
    identity = (args.toolchain_id + ";" if args.toolchain_id else "") + "cmake-cache-sha256:" + cache_hash
    return toolchain(compilers[0], identity)


def verify(binary, abi, receipt, expected, expected_toolchain=None, headers_dir=None):
    if set(receipt) - {"headers"} != {"schemaVersion", "kind", "source", "artifact", "toolchain"} or \
            type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1 or \
            receipt["kind"] != "valhalla-native-artifact":
        raise ProvenanceError("Invalid native artifact receipt schema")
    if source_from(receipt) != expected:
        raise ProvenanceError("Artifact source differs from trusted expected source")
    artifact = receipt["artifact"]
    if not isinstance(artifact, dict) or type(artifact.get("size")) is not int or \
            set(artifact) != {"abi", "name", "size", "sha256"} or artifact != {
            "abi": abi, "name": Path(binary).name, "size": Path(binary).stat().st_size,
            "sha256": file_hash(binary)}:
        raise ProvenanceError("Binary is missing, changed, or labelled with the wrong ABI")
    if "headers" in receipt:
        headers = receipt["headers"]
        if not isinstance(headers, dict) or set(headers) != {"fileCount", "treeSha256"} or \
                type(headers.get("fileCount")) is not int or not 0 < headers["fileCount"] <= 32768:
            raise ProvenanceError("Invalid header tree identity")
        hex_value(headers["treeSha256"], 64)
    if headers_dir is not None and receipt.get("headers") != tree_identity(headers_dir):
        raise ProvenanceError("Headers are missing build authority or differ from built headers")
    compiler = receipt["toolchain"]
    if not isinstance(compiler, dict) or set(compiler) != {
            "id", "compilerName", "compilerSha256", "compilerVersion"}:
        raise ProvenanceError("Invalid toolchain identity")
    hex_value(compiler["compilerSha256"], 64)
    if not all(isinstance(value, str) and value for value in compiler.values()):
        raise ProvenanceError("Empty toolchain identity")
    if expected_toolchain is not None and compiler["id"] != expected_toolchain:
        raise ProvenanceError("Toolchain differs from expected toolchain")
    binary_abi(binary, abi)
    if file_hash(binary) != artifact["sha256"]:
        raise ProvenanceError("Binary changed during ABI verification")
    return receipt


def atomic_write(path, value, replace=False):
    path = Path(path)
    if path.parent.is_symlink() or path.is_symlink():
        raise ProvenanceError("Receipt paths must not be symlinks")
    existed = path.exists()
    previous_hash = None
    if existed:
        existing = load_json(path)
        previous_hash = file_hash(path)
        same_owner = (existing.get("kind"), existing.get("schemaVersion")) == (
            value.get("kind"), value.get("schemaVersion"))
        if value.get("kind") == "valhalla-native-artifact":
            same_owner = same_owner and isinstance(existing.get("artifact"), dict) and all(existing["artifact"].get(k) ==
                value["artifact"][k] for k in ("abi", "name"))
        if not replace or not same_owner:
            raise ProvenanceError("Existing receipt requires explicit same-owner replacement")
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        if existed:
            if file_hash(path) != previous_hash:
                raise ProvenanceError("Receipt changed during replacement")
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)  # This invocation owns only this temporary file.


def emit(args):
    before = load_source(args.source)
    expected = source_from(before)
    current = snapshot(args.repo, args.manifest, args.require_clean)
    if current != before:
        raise ProvenanceError("Build source changed after its pre-build snapshot")
    binary = Path(args.binary)
    compiler = build_toolchain(args)
    headers_dir = getattr(args, "headers_dir", None)
    if binary.stat().st_mtime_ns <= Path(args.source).stat().st_mtime_ns:
        # An unchanged warm output can only reuse an already valid matching receipt.
        old = verify(binary, args.abi, load_json(args.output), expected, compiler["id"], headers_dir)
        if old["toolchain"] != compiler:
            raise ProvenanceError("Prebuilt output uses a different compiler")
        return old
    receipt = {"schemaVersion": 1, "kind": "valhalla-native-artifact", "source": expected,
               "artifact": {"abi": args.abi, "name": binary.name,
                            "size": binary.stat().st_size, "sha256": file_hash(binary)},
               "toolchain": compiler}
    if headers_dir is not None:
        receipt["headers"] = tree_identity(headers_dir)
    verify(binary, args.abi, receipt, expected, headers_dir=headers_dir)
    if snapshot(args.repo, args.manifest, args.require_clean) != before:
        raise ProvenanceError("Source changed while producing the receipt")
    atomic_write(args.output, receipt, args.replace)
    return receipt


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("source", "emit", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest", required=name != "verify")
        sub.add_argument("--require-clean", action="store_true")
        if name != "verify":
            sub.add_argument("--repo", required=True)
            sub.add_argument("--output", required=True)
            sub.add_argument("--replace", action="store_true")
        if name != "source":
            sub.add_argument("--headers-dir")
            sub.add_argument("--binary", required=True)
            sub.add_argument("--abi", required=True, choices=[*ELF_ABIS, *APPLE_ABIS])
        if name == "emit":
            sub.add_argument("--source", required=True)
            compiler = sub.add_mutually_exclusive_group(required=True)
            compiler.add_argument("--compiler")
            compiler.add_argument("--cmake-cache")
            sub.add_argument("--toolchain-id")
        if name == "verify":
            sub.add_argument("--receipt", required=True)
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--expected-source")
            group.add_argument("--expected-wrapper-revision")
            sub.add_argument("--expected-toolchain-id")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        if args.action == "source":
            result = snapshot(args.repo, args.manifest, args.require_clean)
            atomic_write(args.output, result, args.replace)
        elif args.action == "emit":
            result = emit(args)
        else:
            if args.expected_source:
                expected = source_from(load_source(args.expected_source))
            else:
                if not args.require_clean or not args.manifest:
                    raise ProvenanceError("CI revision verification requires trusted manifest and --require-clean")
                expected = expected_source(args.expected_wrapper_revision, manifest(args.manifest))
            if args.require_clean and not expected["clean"]:
                raise ProvenanceError("Clean source required")
            result = verify(args.binary, args.abi, load_json(args.receipt), expected,
                            args.expected_toolchain_id, args.headers_dir)
        print(canonical(result).decode(), end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print("Native provenance rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
