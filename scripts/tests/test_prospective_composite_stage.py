"""The reviewed additive group must retain its global windows and source indices."""
import pathlib
import sys
import tempfile
import unittest
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
import prospective_trace_capture as capture


class CompositeStageTests(unittest.TestCase):
    def test_exact_combined_inventory_retains_each_source_window(self):
        entries = capture.combined_entries(ROOT/'test-fixtures/prospective-v1')
        self.assertEqual(len(entries),143)
        self.assertEqual([row['index'] for _, row in entries],list(range(143)))
        rows = capture.admit_composite_requests(ROOT/'test-fixtures/prospective-composite-v1')
        self.assertEqual(len(rows),33)
        for (_, staged), source in zip(entries[110:],rows):
            for key in ('fixtureId','variant','split','window','windowIndex','sourceInputIndices','originalSourceIndices'):
                self.assertEqual(staged[key],source[key])
        tail = [row for row in rows if row['fixtureId']=='v4-multiwindow-193-short-tail'
                and row['variant']=='original' and row['window']['endIndex']==192]
        self.assertEqual(len(tail),1)
        self.assertEqual(tail[0]['sourceInputIndices'],list(range(184,193)))
        self.assertGreater(tail[0]['windowIndex'],0)

    def test_missing_duplicate_renumbered_or_extra_manifest_rows_refused(self):
        import json
        for mutation in ('missing','duplicate','renumber','extra'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as temp:
                root=pathlib.Path(temp)/'requests'
                shutil.copytree(ROOT/'test-fixtures/prospective-composite-v1',root)
                path=root/'manifest.json'
                manifest=json.loads(path.read_text())
                rows=manifest['contract']['requests']
                if mutation=='missing': rows.pop()
                elif mutation=='duplicate': rows[-1]=rows[0]
                elif mutation=='extra': rows.append(rows[0])
                else:
                    tail=next(row for row in rows if row['window']['startIndex']>0)
                    tail['windowIndex']=0
                    tail['window']={'startIndex':0,'endIndex':len(tail['sourceInputIndices'])-1}
                    tail['sourceInputIndices']=list(range(len(tail['sourceInputIndices'])))
                # Recomputing producer metadata cannot replace the independently pinned contract.
                manifest['contractSha256']=capture.sha(capture.encoded(manifest['contract']))
                path.write_bytes(capture.encoded(manifest))
                with self.assertRaises(capture.InvalidCapture):
                    capture.admit_composite_requests(root)


if __name__=='__main__': unittest.main()
