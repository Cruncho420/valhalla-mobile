"""Importer-only trace requests remain exact, bounded, and separately attributable."""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import prospective_trace_capture as capture


class ImporterStageTests(unittest.TestCase):
    def test_exact_importer_requests_extend_the_combined_inventory(self):
        rows = capture.admit_importer_requests(ROOT / 'test-fixtures/prospective-importer-v1')
        self.assertEqual(len(rows), 22)
        entries = capture.combined_entries(ROOT / 'test-fixtures/prospective-v1')
        self.assertEqual(len(entries), 283)
        for (_, staged), source in zip(entries[261:], rows):
            for key in ('fixtureId', 'variant', 'split', 'sourcePointCount', 'selectedPointCount',
                        'action', 'windowIndex', 'window', 'sourceInputIndices'):
                self.assertEqual(staged[key], source[key])
            self.assertEqual(staged['request'], source['request'])
            self.assertEqual(staged['group'], 'importer-v1')

    def test_changed_index_authority_action_or_identity_is_refused(self):
        for mutation in ('index', 'source-count', 'action', 'duplicate', 'authority'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp) / 'importer'
                shutil.copytree(ROOT / 'test-fixtures/prospective-importer-v1', root)
                manifest_path = root / 'manifest.json'
                manifest = json.loads(manifest_path.read_text())
                contract = manifest['contract']
                rows = contract['requests']
                if mutation == 'index':
                    rows[0]['sourceInputIndices'][1] = rows[0]['sourceInputIndices'][0]
                elif mutation == 'source-count':
                    rows[0]['sourcePointCount'] = rows[0]['sourceInputIndices'][-1]
                elif mutation == 'action':
                    rows[0]['action'] = 'trace_route'
                elif mutation == 'duplicate':
                    rows[-1] = rows[0]
                else:
                    contract['authority']['preparationSha256'] = '0' * 64
                manifest['contractSha256'] = capture.sha(capture.production_encoded(contract))
                manifest_path.write_bytes(capture.production_encoded(manifest))
                with self.assertRaises(capture.InvalidCapture):
                    capture.admit_importer_requests(root)

    def test_changed_request_bytes_and_extra_files_are_refused(self):
        for mutation in ('bytes', 'extra'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp) / 'importer'
                shutil.copytree(ROOT / 'test-fixtures/prospective-importer-v1', root)
                if mutation == 'bytes':
                    request = next(root.glob('*.trace_attributes.json'))
                    request.write_bytes(request.read_bytes() + b'\n')
                else:
                    (root / 'unexpected.json').write_text('{}')
                with self.assertRaises(capture.InvalidCapture):
                    capture.admit_importer_requests(root)


if __name__ == '__main__':
    unittest.main()
