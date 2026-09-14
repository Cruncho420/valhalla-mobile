"""Source-only whole-trip capture identity; no native acceptance outcome is assumed."""
import pathlib
import sys
import tempfile
import unittest
import json
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import prospective_trace_capture as capture


class TripStageTests(unittest.TestCase):
    def test_exact_trip_requests_and_combined_action_inventory(self):
        rows=capture.admit_trip_requests(ROOT/'test-fixtures/prospective-trip-v1')
        self.assertEqual(len(rows),118)
        entries=capture.combined_entries(ROOT/'test-fixtures/prospective-v1')
        self.assertEqual(len(entries),283)
        self.assertEqual(capture.CAPTURE_FILES,569)
        self.assertEqual(sum(row.get('action','trace_attributes')=='trace_attributes' for _,row in entries),165)
        self.assertEqual(sum(row.get('action')=='trace_route' for _,row in entries),118)
        for (_,entry),row in zip(entries[143:],rows):
            for key in ('fixtureId','variant','split','action','windowIndex','window','sourceInputIndices'):
                self.assertEqual(entry[key],row[key])
            raw=(ROOT/'test-fixtures/prospective-trip-v1'/row['request']['file']).read_bytes()
            request=json.loads(raw)
            self.assertEqual(set(request),{'shape','costing','shape_match'})
            self.assertEqual(row['sourceInputIndices'],list(range(len(request['shape']))))
            self.assertEqual(entry['sourceGroup'],row['group'])

    def test_collectors_dispatch_the_pinned_action_without_translation(self):
        swift=(ROOT/'apple/Tests/ValhallaTests/TestProspectiveTraceCapture.swift').read_text()
        kotlin=(ROOT/'android/valhalla/src/androidTest/java/com/valhalla/valhalla/ValhallaProspectiveTraceCaptureTest.kt').read_text()
        for source in (swift,kotlin):
            self.assertIn('trace_route',source)
            self.assertIn('trace_attributes',source)
            self.assertIn('traceRoute',source)
            self.assertIn('traceAttributes',source)
            self.assertIn('283',source)
        self.assertIn('row["request"] != nil', swift)
        self.assertIn('row.has("request")', kotlin)

    def test_missing_duplicate_reordered_or_wrong_action_cannot_replace_frozen_manifest(self):
        for mutation in ('missing','duplicate','reordered','wrong-action'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=pathlib.Path(temp)/'trip'
                shutil.copytree(ROOT/'test-fixtures/prospective-trip-v1',root)
                path=root/'manifest.json';manifest=json.loads(path.read_text())
                rows=manifest['contract']['requests']
                if mutation=='missing':rows.pop()
                elif mutation=='duplicate':rows[-1]=rows[0]
                elif mutation=='reordered':rows.reverse()
                else:rows[0]['action']='trace_attributes'
                manifest['contractSha256']=capture.sha(capture.encoded(manifest['contract']))
                path.write_bytes(capture.encoded(manifest))
                with self.assertRaises(capture.InvalidCapture):capture.admit_trip_requests(root)

    def test_request_byte_change_and_extra_file_are_refused(self):
        for change in ('bytes','extra'):
            with tempfile.TemporaryDirectory() as temp:
                root=pathlib.Path(temp)/'trip'
                shutil.copytree(ROOT/'test-fixtures/prospective-trip-v1',root)
                if change=='bytes':
                    p=next(root.glob('*.trace_route.json'));p.write_bytes(p.read_bytes()+b'\n')
                else:(root/'extra.json').write_text('{}')
                with self.assertRaises(capture.InvalidCapture):capture.admit_trip_requests(root)


if __name__=='__main__':unittest.main()
