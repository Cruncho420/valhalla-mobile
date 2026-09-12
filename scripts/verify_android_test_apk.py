"""Verify the test APK against independently reverified AAR native bytes before installation."""
import argparse
import hashlib
from pathlib import Path
import stat
import sys
import zipfile

sys.dont_write_bytecode = True
import package_android_native as transport

P = transport.P


def verify(repo, aar, apk, output, strip_tool, aar_receipt=None):
    repo, apk = Path(repo), Path(apk)
    before = P.file_hash(apk)
    with transport.scratch(repo) as work:
        work = Path(work)
        package_proof = work / "aar.json"
        transport.verify_aar(repo, aar, package_proof, strip_tool)
        package = P.load_json(package_proof)
        if aar_receipt is not None and P.load_json(aar_receipt) != package:
            raise P.ProvenanceError("Downloaded AAR receipt differs from independent verification")
        expected = {f"lib/{a}/{transport.BINARY}": a for a in transport.ABIS}
        observed = {}
        with zipfile.ZipFile(apk) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            matching = {n for n in names if n.endswith("/" + transport.BINARY)}
            if len(names) != len(set(names)) or matching != set(expected):
                raise P.ProvenanceError("Test APK native libraries are missing, duplicated, or misplaced")
            for name, abi in expected.items():
                info = archive.getinfo(name)
                target = package["transforms"][abi]
                if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG) or \
                        info.file_size != target["outputSize"]:
                    raise P.ProvenanceError("Test APK native type or size differs from verified AAR")
                digest = hashlib.sha256()
                with archive.open(info) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != target["outputSha256"]:
                    raise P.ProvenanceError("Test APK native bytes differ from verified AAR")
                observed[abi] = {"sha256": digest.hexdigest(), "size": info.file_size}
        if P.file_hash(apk) != before or P.file_hash(aar) != package["aar"]["sha256"]:
            raise P.ProvenanceError("APK or AAR changed during verification")
        receipt = {"schemaVersion": 1, "kind": "valhalla-android-test-apk",
                   "apk": {"name": apk.name, "size": apk.stat().st_size, "sha256": before},
                   "aar": package["aar"], "aarVerificationSha256": P.file_hash(package_proof),
                   "native": observed}
        staged = work / "apk.json"
        staged.write_bytes(P.canonical(receipt))
        transport.publish_file(staged, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("repo", "aar", "apk", "output", "strip-tool"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--aar-receipt")
    args = parser.parse_args()
    try:
        verify(args.repo, args.aar, args.apk, args.output, args.strip_tool, args.aar_receipt)
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Test APK rejected: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
