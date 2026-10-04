"""Guard hidden Apple header transport without building native code or weakening authority."""
import io
from pathlib import Path
import re
import unittest
import zipfile
import test_native_artifact_provenance as fixtures

P = fixtures.P
WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/rods-release.yml'


class AppleHeaderTransportTests(unittest.TestCase):
    setUp = fixtures.ProvenanceTests.setUp
    git = fixtures.ProvenanceTests.git
    init = fixtures.ProvenanceTests.init
    elf = fixtures.ProvenanceTests.elf

    def test_upload_keeps_hidden_files_only_in_explicit_build_inputs(self):
        workflow = WORKFLOW.read_text()
        ios = workflow.split('  build-ios:', 1)[1].split('  create-xcframework:', 1)[0]
        upload = ios.split('      - name: Upload build artifacts', 1)[1]
        self.assertRegex(upload, r'(?m)^          include-hidden-files: true$')
        paths = upload.split('          path: |\n', 1)[1].split('          if-no-files-found:', 1)[0]
        self.assertEqual([line.strip() for line in paths.splitlines()], [
            'build/apple/${{ matrix.arch }}/libvalhalla_all.a',
            'build/apple/${{ matrix.arch }}/libvalhalla_all.a.provenance.json',
            'build/apple/${{ matrix.arch }}/install/include',
        ])
        self.assertEqual(len(re.findall(r'include-hidden-files: true', workflow)), 1)

    def test_lossless_archive_preserves_hidden_headers_and_strict_authority(self):
        headers = self.root / 'headers'
        keep = headers / 'boost/headers/.gitkeep'
        keep.parent.mkdir(parents=True)
        keep.write_bytes(b'')
        (headers / 'wrapper.h').write_bytes(b'header bytes')
        nested = headers / '.generated/config.h'
        nested.parent.mkdir()
        nested.write_bytes(b'generated header')
        self.args.headers_dir = headers
        receipt = P.emit(self.args)
        expected = P.source_from(P.load_json(self.source))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            for path in headers.rglob('*'):
                if path.is_file():
                    zipped.write(path, path.relative_to(headers).as_posix())
        received = self.root / 'received'
        with zipfile.ZipFile(archive) as zipped:
            self.assertIn('boost/headers/.gitkeep', zipped.namelist())
            self.assertIn('.generated/config.h', zipped.namelist())
            zipped.extractall(received)  # Trusted, task-generated names only.
        self.assertEqual(P.verify(self.binary, self.args.abi, receipt, expected,
                                  headers_dir=received), receipt)
        received_keep = received / 'boost/headers/.gitkeep'
        received_keep.unlink()
        with self.assertRaises(P.ProvenanceError):
            P.verify(self.binary, self.args.abi, receipt, expected, headers_dir=received)
        received_keep.write_bytes(b'changed')
        with self.assertRaises(P.ProvenanceError):
            P.verify(self.binary, self.args.abi, receipt, expected, headers_dir=received)
        received_keep.write_bytes(b'')
        self.assertEqual(P.tree_identity(received), receipt['headers'])
        self.assertEqual(P.load_json(self.receipt), receipt)


if __name__ == '__main__':
    unittest.main()
