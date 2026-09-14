#!/usr/bin/env python3
"""Stage reviewed public requests and verify bounded raw capture, never matcher correctness."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REQUEST_SHA = '0729b13a8334f964d72d57ac275c4f4082e8ec5f21cb2b41c6a401d6f9e59abc'
STAGE_CONTRACT = '0cdb9ffb5c45309ff6e1b25fba6675de6cef2b05002e20db6aadce4379acba60'
ZIP_SHA = '4f15fea95e686b0924fc4e14a113f09101ea3ef562ee34682b6da34c771af6ea'
GRAPH_SHA = 'd69510b46c5d1d2663ea0ac095039b80406ad51edb26db016401e7b80aa18c6c'
EXTRACT_SHA = 'c0957c92bb71833ed3763e4b2c42a536cb28f2bcb69c991264532485edee75d4'
COMPOSITE_SHA = 'cba47d133c2d6ff62d95139baca0ed5c5d5295971f9ac486325a10b6aeb6347b'
COMPOSITE_CONTRACT = 'b487eebba7e3743b5909bdfd8edc538b78592ff3b351aff9fe416f2a3d83e477'
COMPOSITE_STAGE_CONTRACT = '110a7465be2e484d15b6d90f941d24a49b00beda9246c7c288407810a35ae279'
GROUPS = [dict(id='single-window-v1', manifestSha256=REQUEST_SHA,
               stageContractSha256=STAGE_CONTRACT, requestCount=110),
          dict(id='composite-v1', manifestSha256=COMPOSITE_SHA,
               requestContractSha256=COMPOSITE_CONTRACT,
               stageContractSha256=COMPOSITE_STAGE_CONTRACT, requestCount=33)]
REQUEST_COUNT = 143
CAPTURE_FILES = 2 * REQUEST_COUNT + 3
MAX_RESPONSE = 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


class InvalidCapture(ValueError):
    pass


def require(condition):
    if not condition:
        raise InvalidCapture('Prospective capture admission failed')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda _: require(False))
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidCapture('Invalid bounded JSON') from None


def read(path, limit):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit)
    with path.open('rb') as file:
        data = file.read(limit + 1)
    require(len(data) <= limit)
    return data


def _admit_requests(root, expected_sha, expected_count):
    raw = read(root / 'manifest.json', 128 * 1024)
    require(sha(raw) == expected_sha)
    manifest = decode(raw)
    rows = manifest['contract']['requests']
    require(len(rows) == expected_count)
    names = {'manifest.json'}
    for row in rows:
        for kind in ('original', 'diagnostic'):
            item = row[kind]
            require(re.fullmatch(r'[a-z0-9-]+\.(original|diagnostic)\.json', item['file']))
            require(item['file'] not in names)
            names.add(item['file'])
            data = read(root / item['file'], 8192)
            require(len(data) == item['bytes'] and sha(data) == item['sha256'])
            decode(data)
    require({p.name for p in root.iterdir()} == names)
    return rows


def admit_requests(root):
    return _admit_requests(root, REQUEST_SHA, 110)


def admit_composite_requests(root):
    return _admit_requests(root, COMPOSITE_SHA, 33)


def combined_entries(single_root):
    composite_root = ROOT / 'test-fixtures/prospective-composite-v1'
    groups = [('single-window-v1', single_root, admit_requests(single_root)),
              ('composite-v1', composite_root, admit_composite_requests(composite_root))]
    entries = []
    for group_id, root, rows in groups:
        for row in rows:
            # Explicit composite window/source indices are never flattened or renumbered.
            entry = dict(row, index=len(entries), group=group_id)
            if group_id == 'single-window-v1':
                entry['windowIndex'] = 0
            entries.append((root, entry))
    require(len(entries) == REQUEST_COUNT)
    return entries


def stage(requests, archive, destination):
    require(not destination.exists() and not destination.is_symlink())
    rows = combined_entries(requests)
    zip_bytes = read(archive, 16 * 1024 * 1024)
    require(sha(zip_bytes) == ZIP_SHA)
    # This exact reviewed ZIP digest binds the authenticated retained source run.
    # Do not generalize this into accepting a producer-supplied hash or rebuilt graph.
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as source:
        name = 'osm-region-extractor/osm-region-extractor/evidence/source/graph.tar.gz'
        require(source.getinfo(name).file_size == 1304748)
        compressed = source.read(name)
    require(sha(compressed) == GRAPH_SHA)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        graph = stream.read(4 * 1024 * 1024 + 1)
    require(len(graph) == 3051520 and sha(graph) == EXTRACT_SHA)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prospective-stage-', dir=destination.parent) as temp:
        private = Path(temp) / 'stage'
        private.mkdir()
        (private / 'graph.tar').write_bytes(graph)
        template = read(ROOT / 'android/valhalla/src/androidTest/assets/config.json', 64 * 1024)
        (private / 'config-template.json').write_bytes(template)
        (private / 'request-manifest.json').write_bytes(read(requests / 'manifest.json', 128 * 1024))
        (private / 'composite-request-manifest.json').write_bytes(read(ROOT / 'test-fixtures/prospective-composite-v1/manifest.json', 128 * 1024))
        entries = []
        for index, (request_root, row) in enumerate(rows):
            entry = dict(row)
            for kind in ('original', 'diagnostic'):
                data = read(request_root / row[kind]['file'], 8192)
                filename = f'{index:03d}.{kind}.json'
                (private / filename).write_bytes(data)
                entry[kind] = dict(row[kind], file=filename)
            entries.append(entry)
        manifest = dict(version=1, groups=GROUPS, stageContractSha256=STAGE_CONTRACT,
                        requestManifestSha256=REQUEST_SHA, sourceArtifactId=10341602724,
                        sourceRunId=34828738835, sourceAttestationId=47303160,
                        sourceZipSha256=ZIP_SHA, graphSha256=GRAPH_SHA,
                        extractSha256=EXTRACT_SHA, configTemplateSha256=sha(template), rows=entries)
        (private / 'stage.json').write_bytes(encoded(manifest))
        # Atomic publication after all bounded input verification; never replace prior evidence.
        require(not destination.exists())
        os.rename(private, destination)
    return {'stageSha256': sha(encoded(manifest)), 'requestCount': len(rows)}


def verify(stage_dir, output, platform):
    stage_bytes = read(stage_dir / 'stage.json', 256 * 1024)
    planned = decode(stage_bytes)
    require(planned['requestManifestSha256'] == REQUEST_SHA)
    require(planned['groups'] == GROUPS)
    require(planned['stageContractSha256'] == STAGE_CONTRACT)
    require(planned['sourceArtifactId'] == 10341602724 and planned['sourceRunId'] == 34828738835)
    require(planned['sourceAttestationId'] == 47303160)
    require(planned['sourceZipSha256'] == ZIP_SHA and planned['graphSha256'] == GRAPH_SHA)
    require(planned['configTemplateSha256'] == sha(read(ROOT / 'android/valhalla/src/androidTest/assets/config.json', 65536)))
    # Independently re-admit frozen source; an edited stage cannot choose its own plan.
    original_rows = combined_entries(ROOT / 'test-fixtures/prospective-v1')
    expected_rows = []
    for index, (_, row) in enumerate(original_rows):
        expected = dict(row)
        for kind in ('original', 'diagnostic'):
            expected[kind] = dict(row[kind], file=f'{index:03d}.{kind}.json')
            data = read(stage_dir / expected[kind]['file'], 8192)
            require(sha(data) == expected[kind]['sha256'])
        expected_rows.append(expected)
    require(planned['rows'] == expected_rows and planned['extractSha256'] == EXTRACT_SHA)
    require(sha(read(stage_dir / 'graph.tar', 4 * 1024 * 1024)) == EXTRACT_SHA)
    require(sha(read(stage_dir / 'config-template.json', 64 * 1024)) == planned['configTemplateSha256'])
    files = list(output.iterdir())
    require(len(files) == CAPTURE_FILES and sum(p.stat().st_size for p in files) <= MAX_TOTAL)
    receipt = decode(read(output / 'receipt.json', 256 * 1024))
    require(receipt['version'] == 1 and receipt['complete'] is True)
    require(receipt['stageSha256'] == sha(stage_bytes) and receipt['extractSha256'] == EXTRACT_SHA)
    require(receipt['platform'] == platform)
    allowed = {'android': {'x86_64'}, 'ios': {'arm64-ios-simulator', 'x64-ios-simulator'}}
    require(receipt['abi'] in allowed[platform])
    config_bytes = read(output / 'config.json', 64 * 1024)
    require(receipt['configSha256'] == sha(config_bytes))
    expected_config = decode(read(stage_dir / 'config-template.json', 64 * 1024))
    actual_config = decode(config_bytes)
    tile = actual_config['mjolnir']['tile_extract']
    require(isinstance(tile, str) and tile.startswith('/') and tile.endswith('/graph.tar'))
    expected_config['mjolnir']['tile_extract'] = tile
    require(actual_config == expected_config)
    require(read(output / 'stage.json', 256 * 1024) == stage_bytes)
    require(len(receipt['rows']) == len(expected_rows))
    names = {'receipt.json', 'config.json', 'stage.json'}
    for index, (actual, expected) in enumerate(zip(receipt['rows'], expected_rows)):
        require(actual['identity'] == expected)
        request_name, response_name = f'{index:03d}.request.json', f'{index:03d}.response.raw'
        names.update((request_name, response_name))
        request = read(output / request_name, 8192)
        response = read(output / response_name, MAX_RESPONSE)
        require(request == read(stage_dir / expected['diagnostic']['file'], 8192))
        require(actual['requestSha256'] == sha(request) and actual['requestBytes'] == len(request))
        require(actual['responseSha256'] == sha(response) and actual['responseBytes'] == len(response))
        require(actual['returned'] is True)
    require({p.name for p in files} == names)
    return dict(version=1, complete=True, requestCount=REQUEST_COUNT, platform=platform, abi=receipt['abi'],
                stageSha256=sha(stage_bytes), receiptSha256=sha(read(output / 'receipt.json', 256*1024)),
                matcherCorrectnessAdmitted=False, consumerPreservationAdmitted=False,
                binaryProvenanceAdmitted=False)


def unpack_capture(archive, destination):
    require(not destination.exists())
    require(archive.stat().st_size <= MAX_TOTAL)
    # Only flat files from the one explicitly named private collector directory.
    with tarfile.open(archive, 'r:') as stream:
        seen = set()
        members = []
        total = 0
        for index, member in enumerate(stream):
            require(index < CAPTURE_FILES + 1 and member.name not in seen)
            seen.add(member.name)
            if member.isdir():
                require(member.name == 'files/prospective-capture')
                continue
            require(member.isfile() and re.fullmatch(r'files/prospective-capture/[A-Za-z0-9.-]+', member.name))
            total += member.size
            require(0 <= member.size <= MAX_RESPONSE and total <= MAX_TOTAL)
            members.append((Path(member.name).name, stream.extractfile(member).read(MAX_RESPONSE + 1)))
    destination.mkdir(parents=True)
    for name, data in members:
        (destination / name).write_bytes(data)


def publish(private_root, destination):
    """Publish bounded raw evidence only; build logs and result bundles remain private."""
    require(not destination.exists() and not destination.is_symlink())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prospective-publication-', dir=destination.parent) as temp:
        staged = Path(temp) / 'published'
        staged.mkdir()
        status = 'INCOMPLETE'
        try:
            raw = private_root / 'raw'
            require(raw.is_dir() and not raw.is_symlink())
            count, total = 0, 0
            for path in raw.iterdir():
                count += 1
                require(count <= CAPTURE_FILES)
                require(re.fullmatch(r'(?:[0-9]{3}\.(?:request\.json|response\.raw)|receipt\.json|config\.json|stage\.json)', path.name))
                data = read(path, MAX_RESPONSE)
                total += len(data)
                require(total <= MAX_TOTAL)
                (staged / path.name).write_bytes(data)
            for name in ('provenance.json', 'capture-verification.json'):
                path = private_root / name
                if path.exists():
                    data = read(path, 65536)
                    decode(data)
                    total += len(data)
                    require(total <= MAX_TOTAL)
                    (staged / name).write_bytes(data)
            if all((staged / name).exists() for name in ('receipt.json', 'provenance.json', 'capture-verification.json')):
                receipt_bytes = read(staged / 'receipt.json', 262144)
                receipt = decode(receipt_bytes)
                proof = decode(read(staged / 'provenance.json', 65536))
                verification = decode(read(staged / 'capture-verification.json', 65536))
                if (receipt.get('complete') is True and verification.get('complete') is True
                        and proof.get('captureReceiptSha256') == sha(receipt_bytes)
                        and proof.get('stageSha256') == verification.get('stageSha256')):
                    status = 'CAPTURE_COMPLETE_UNCERTIFIED'
        except (InvalidCapture, OSError, ValueError, KeyError, TypeError):
            # Only this private, newly created publication scratch is discarded.
            import shutil
            shutil.rmtree(staged)
            staged.mkdir()
        (staged / 'publication.json').write_bytes(encoded(dict(version=1, status=status,
                    matcherCorrectnessAdmitted=False, consumerPreservationAdmitted=False,
                    productionReadinessAdmitted=False)))
        require(not destination.exists())
        os.rename(staged, destination)
    return {'status': status}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('stage')
    prepare.add_argument('--requests', type=Path, default=ROOT / 'test-fixtures/prospective-v1')
    prepare.add_argument('--archive', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('--stage', type=Path, required=True)
    check.add_argument('--capture', type=Path, required=True)
    check.add_argument('--platform', choices=['ios', 'android'], required=True)
    unpack = commands.add_parser('unpack')
    unpack.add_argument('--archive', type=Path, required=True)
    unpack.add_argument('--output', type=Path, required=True)
    publication = commands.add_parser('publish')
    publication.add_argument('--private', type=Path, required=True)
    publication.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'stage':
            result = stage(args.requests, args.archive, args.output)
        elif args.command == 'publish':
            result = publish(args.private, args.output)
        elif args.command == 'unpack':
            unpack_capture(args.archive, args.output)
            result = {'unpacked': True}
        else:
            result = verify(args.stage, args.capture, args.platform)
        print(json.dumps(result, sort_keys=True))
    except (InvalidCapture, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, tarfile.TarError):
        print('Prospective capture incomplete or invalid', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
