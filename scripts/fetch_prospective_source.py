#!/usr/bin/env python3
"""Fetch only the reviewed retained artifact; never rebuild or select a latest graph."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
from urllib.parse import urlsplit

from prospective_trace_capture import ZIP_SHA, GRAPH_SHA, sha, require, encoded, decode

REPO = 'Cruncho420/osm-region-extractor'


def command(argv, limit):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        chunks, total = [], 0
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            require(total <= limit)
            chunks.append(chunk)
        require(process.wait(timeout=90) == 0)
        return b''.join(chunks)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def fetch(output):
    require(not output.exists())
    metadata = decode(command(['gh', 'api', f'repos/{REPO}/actions/artifacts/10341602724'], 65536))
    require(metadata['id'] == 10341602724 and metadata['expired'] is False)
    require(metadata['name'] == 'valhalla-andorra-source-evidence-34828738835')
    require(metadata['workflow_run']['id'] == 34828738835)
    require(metadata['workflow_run']['head_sha'] == '8d7a40f450e32efdec0c2c41863b768085593579')
    require(metadata['digest'] == 'sha256:' + ZIP_SHA and metadata['size_in_bytes'] == 8687498)
    attested = decode(command(['gh', 'api', f'repos/{REPO}/attestations/sha256:{GRAPH_SHA}'], 1048576))
    require(any(urlsplit(item['bundle_url']).path.endswith('/47303160.json.sn')
                for item in attested['attestations']))
    archive = command(['gh', 'api', f'repos/{REPO}/actions/artifacts/10341602724/zip'], 16*1048576)
    require(sha(archive) == ZIP_SHA)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        graph = source.read('osm-region-extractor/osm-region-extractor/evidence/source/graph.tar.gz')
    require(sha(graph) == GRAPH_SHA)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prospective-fetch-', dir=output.parent) as temporary:
        private = Path(temporary) / 'source'
        private.mkdir()
        (private / 'artifact.zip').write_bytes(archive)
        graph_path = private / 'graph.tar.gz'
        graph_path.write_bytes(graph)
        proof = command(['gh', 'attestation', 'verify', str(graph_path), '--repo', REPO,
                         '--signer-workflow', REPO + '/.github/workflows/valhalla-source-evidence.yml',
                         '--signer-digest', '8d7a40f450e32efdec0c2c41863b768085593579',
                         '--source-digest', '8d7a40f450e32efdec0c2c41863b768085593579',
                         '--format', 'json'], 1048576)
        require(isinstance(decode(proof), list) and len(decode(proof)) > 0)
        (private / 'graph-attestation-verification.json').write_bytes(proof)
        # Avoid storing transient signed download URLs or credentials from API responses.
        identity = {key: metadata[key] for key in ('id', 'name', 'size_in_bytes', 'digest', 'expired', 'workflow_run')}
        (private / 'artifact-metadata.json').write_bytes(encoded(identity))
        require(not output.exists())
        os.rename(private, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        fetch(args.output)
    except Exception:
        raise SystemExit('Reviewed prospective source unavailable or invalid') from None
