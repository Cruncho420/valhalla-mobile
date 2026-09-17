"""Host admission only: synthetic responses are never native outcomes."""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('capture', ROOT / 'scripts/prospective_trace_capture.py')
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureTests(unittest.TestCase):
    def test_frozen_inventory(self):
        rows = capture.admit_requests(ROOT / 'test-fixtures/prospective-v1')
        self.assertEqual(len(rows), 110)

    def test_modified_request_is_refused(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / 'requests'
            shutil.copytree(ROOT / 'test-fixtures/prospective-v1', root)
            path = next(root.glob('*.diagnostic.json'))
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaises(capture.InvalidCapture):
                capture.admit_requests(root)

    def test_extra_or_missing_file_is_refused(self):
        import shutil
        for extra in (True, False):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp) / 'requests'
                shutil.copytree(ROOT / 'test-fixtures/prospective-v1', root)
                if extra:
                    (root / 'decoy.json').write_text('{}')
                else:
                    next(root.glob('*.original.json')).unlink()
                with self.assertRaises(capture.InvalidCapture):
                    capture.admit_requests(root)

    def test_duplicate_json_keys_refused(self):
        with self.assertRaises(capture.InvalidCapture):
            capture.decode(b'{"a":1,"a":2}')

    def test_nonfinite_json_refused(self):
        with self.assertRaises(capture.InvalidCapture):
            capture.decode(b'{"a":NaN}')

    def test_capture_mutations(self):
        from unittest.mock import patch
        import copy
        import json
        # These are explicit synthetic raw returns, including a refusal and alternates.
        # They test provenance/completeness only, never produce an admitted native receipt.
        with tempfile.TemporaryDirectory() as temp:
            stage = pathlib.Path(temp) / 'stage'
            output = pathlib.Path(temp) / 'output'
            stage.mkdir()
            output.mkdir()
            graph = b'synthetic non-native graph'
            template = (ROOT / 'android/valhalla/src/androidTest/assets/config.json').read_bytes()
            (stage / 'graph.tar').write_bytes(graph)
            (stage / 'config-template.json').write_bytes(template)
            rows = []
            for index, (request_root, row) in enumerate(capture.combined_entries(ROOT / 'test-fixtures/prospective-v1')):
                rows.append(capture.staged_row(request_root, row, index, stage))
            plan = dict(requestManifestSha256=capture.REQUEST_SHA, rows=rows, groups=capture.GROUPS,
                        tripRequestManifestSha256=capture.TRIP_SHA,
                        importerRequestManifestSha256=capture.IMPORTER_SHA,
                        stageContractSha256=capture.STAGE_CONTRACT, sourceArtifactId=10341602724,
                        sourceRunId=34828738835, sourceAttestationId=47303160,
                        sourceZipSha256=capture.ZIP_SHA, graphSha256=capture.GRAPH_SHA,
                        extractSha256=capture.sha(graph), configTemplateSha256=capture.sha(template))
            raw_stage = capture.encoded(plan)
            (stage / 'stage.json').write_bytes(raw_stage)
            (output / 'stage.json').write_bytes(raw_stage)
            config_object = json.loads(template)
            config_object['mjolnir']['tile_extract'] = '/private/test/graph.tar'
            config = capture.encoded(config_object)
            (output / 'config.json').write_bytes(config)
            captured = []
            for index, row in enumerate(rows):
                request = (stage / capture.execution_item(row)['file']).read_bytes()
                response = b'{"code":171}' if index == 0 else b'{"alternates":[{"raw_score":1}]}'
                (output / f'{index:03d}.request.json').write_bytes(request)
                (output / f'{index:03d}.response.raw').write_bytes(response)
                captured.append(dict(identity=row, requestSha256=capture.sha(request), requestBytes=len(request),
                                     responseSha256=capture.sha(response), responseBytes=len(response), returned=True))
            receipt = dict(version=1, complete=True, stageSha256=capture.sha(raw_stage),
                           extractSha256=capture.sha(graph), platform='android', abi='x86_64',
                           configSha256=capture.sha(config), rows=captured)
            with patch.object(capture, 'EXTRACT_SHA', capture.sha(graph)):
                def save(value):
                    (output / 'receipt.json').write_bytes(capture.encoded(value))
                save(receipt)
                result = capture.verify(stage, output, 'android')
                self.assertTrue(result['complete'])
                self.assertFalse(result['matcherCorrectnessAdmitted'])
                self.assertFalse(result['consumerPreservationAdmitted'])
                mutations = [
                    lambda r: r.update(complete=False),
                    lambda r: r.update(abi='arm64-v8a'),
                    lambda r: r.update(stageSha256='0'*64),
                    lambda r: r['rows'].pop(),
                    lambda r: r['rows'].reverse(),
                    lambda r: r['rows'][0].update(returned=False),
                    lambda r: r['rows'][0].update(responseSha256='0'*64),
                    lambda r: r['rows'][0]['identity'].update(windowIndex=1),
                    lambda r: r['rows'][0]['identity'].update(variant='decoy'),
                ]
                for mutate in mutations:
                    changed = copy.deepcopy(receipt)
                    mutate(changed)
                    save(changed)
                    with self.assertRaises(capture.InvalidCapture):
                        capture.verify(stage, output, 'android')
                save(receipt)
                # A fully self-consistent edited stage/receipt cannot renumber a global tail.
                import copy
                for field in ('windowIndex', 'window', 'sourceInputIndices', 'originalSourceIndices'):
                    altered_plan = copy.deepcopy(plan)
                    tail = next(row for row in altered_plan['rows']
                                if row['group'] == 'composite-v1' and row['window']['startIndex'] > 0)
                    if field == 'windowIndex': tail[field] = 0
                    elif field == 'window': tail[field] = dict(startIndex=0, endIndex=len(tail['sourceInputIndices'])-1)
                    else: tail[field] = list(range(len(tail[field])))
                    altered_stage = capture.encoded(altered_plan)
                    (stage / 'stage.json').write_bytes(altered_stage)
                    (output / 'stage.json').write_bytes(altered_stage)
                    altered_receipt = copy.deepcopy(receipt)
                    altered_receipt['stageSha256'] = capture.sha(altered_stage)
                    altered_receipt['rows'][tail['index']]['identity'] = tail
                    save(altered_receipt)
                    with self.assertRaises(capture.InvalidCapture):
                        capture.verify(stage, output, 'android')
                altered_plan = copy.deepcopy(plan)
                importer = next(row for row in altered_plan['rows'] if row['group'] == 'importer-v1')
                importer['sourceInputIndices'][1] = importer['sourceInputIndices'][0]
                altered_stage = capture.encoded(altered_plan)
                (stage / 'stage.json').write_bytes(altered_stage)
                (output / 'stage.json').write_bytes(altered_stage)
                altered_receipt = copy.deepcopy(receipt)
                altered_receipt['stageSha256'] = capture.sha(altered_stage)
                altered_receipt['rows'][importer['index']]['identity'] = importer
                save(altered_receipt)
                with self.assertRaises(capture.InvalidCapture):
                    capture.verify(stage, output, 'android')
                (stage / 'stage.json').write_bytes(raw_stage)
                (output / 'stage.json').write_bytes(raw_stage)
                # The previously admitted110 alone can no longer satisfy the combined contract.
                altered_receipt = copy.deepcopy(receipt)
                altered_receipt['rows'] = altered_receipt['rows'][:110]
                save(altered_receipt)
                with self.assertRaises(capture.InvalidCapture):
                    capture.verify(stage, output, 'android')
                save(receipt)
                # Fail-first: a refusal row is admitted only when it names the native failure
                # class and carries no bytes, and an oversize marker is never a complete capture.
                first = (output / '000.response.raw').read_bytes()
                (output / '000.response.raw').write_bytes(b'')
                refusal = copy.deepcopy(receipt)
                refusal['rows'][0].update(returned=False, failureClass='java.lang.IllegalStateException',
                                          responseSha256=capture.sha(b''), responseBytes=0)
                save(refusal)
                self.assertEqual(capture.verify(stage, output, 'android')['refusedCount'], 1)
                for mutate in (lambda r: r['rows'][0].pop('failureClass'),
                               lambda r: r['rows'][0].update(returned=True),
                               lambda r: r['rows'][0].update(oversize=True)):
                    changed = copy.deepcopy(refusal)
                    mutate(changed)
                    save(changed)
                    with self.assertRaises(capture.InvalidCapture):
                        capture.verify(stage, output, 'android')
                (output / '000.response.raw').write_bytes(first)
                save(receipt)
                path = output / '000.response.raw'
                original = path.read_bytes()
                for changed in [original + b' ', b'x' * (capture.MAX_RESPONSE + 1)]:
                    path.write_bytes(changed)
                    with self.assertRaises(capture.InvalidCapture):
                        capture.verify(stage, output, 'android')
                path.write_bytes(original)
                (output / 'decoy.raw').write_bytes(b'')
                with self.assertRaises(capture.InvalidCapture):
                    capture.verify(stage, output, 'android')

    def test_stage_refuses_existing_output_and_unreviewed_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            archive = root / 'bad.zip'
            archive.write_bytes(b'not the reviewed graph archive')
            for output in (root, root / 'output'):
                with self.assertRaises(capture.InvalidCapture):
                    capture.stage(ROOT / 'test-fixtures/prospective-v1', archive, output)
                self.assertFalse((root / 'output').exists())

    def test_archive_refuses_unsafe_paths_and_duplicates(self):
        import io
        import tarfile
        for names in [('../outside',), ('files/prospective-capture/x.raw',)*2]:
            with tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                archive = root / 'capture.tar'
                with tarfile.open(archive, 'w') as stream:
                    for name in names:
                        info = tarfile.TarInfo(name)
                        stream.addfile(info, io.BytesIO(b''))
                with self.assertRaises(capture.InvalidCapture):
                    capture.unpack_capture(archive, root / 'output')
                self.assertFalse((root / 'output').exists())

    def test_publication_keeps_partial_raw_and_excludes_build_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            private = root/'private'
            (private/'raw').mkdir(parents=True)
            (private/'raw/000.response.raw').write_bytes(b'{"code":171}')
            (private/'xcodebuild.log').write_text('not a capture artifact')
            result = capture.publish(private, root/'published')
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertEqual({p.name for p in (root/'published').iterdir()}, {'000.response.raw','publication.json'})
            with self.assertRaises(capture.InvalidCapture):
                capture.publish(private, root/'published')

    def test_publication_salvages_capture_tar_when_unpack_left_no_raw(self):
        # A malformed collector tar must not cost a 45-minute build its only raw evidence.
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / 'private').mkdir()
            (root / 'private/capture.tar').write_bytes(b'truncated collector archive')
            result = capture.publish(root / 'private', root / 'published')
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertEqual({p.name for p in (root / 'published').iterdir()},
                             {'capture.tar', 'publication.json'})
            import json
            publication = json.loads((root / 'published/publication.json').read_text())
            self.assertTrue(publication['salvagedCaptureTar'])
            self.assertEqual(publication['failure'], 'InvalidCapture')
            self.assertEqual((root / 'published/capture.tar').read_bytes(), b'truncated collector archive')

    def test_publication_keeps_already_staged_raw_when_a_later_file_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / 'private/raw').mkdir(parents=True)
            (root / 'private/raw/000.response.raw').write_bytes(b'{"code":171}')
            (root / 'private/raw/receipt.json').write_bytes(b'{"complete":false}')
            (root / 'private/provenance.json').write_bytes(b'not json')
            result = capture.publish(root / 'private', root / 'published')
            self.assertEqual(result['status'], 'INCOMPLETE')
            self.assertEqual({p.name for p in (root / 'published').iterdir()},
                             {'000.response.raw', 'receipt.json', 'publication.json'})

    def test_trip_requests_require_production_costing_and_shape_match(self):
        # The frozen hashes make a tampered body unreachable in production, so this rebuilds a
        # self-consistent decoy manifest and pins it, proving the field check itself refuses.
        import json
        import shutil
        from unittest.mock import patch

        def rebuild(temp, mutate):
            root = pathlib.Path(temp) / 'trip'
            shutil.copytree(ROOT / 'test-fixtures/prospective-trip-v1', root)
            manifest = json.loads((root / 'manifest.json').read_text())
            row = manifest['contract']['requests'][0]
            path = root / row['request']['file']
            body = json.loads(path.read_text())
            mutate(body)
            data = capture.production_encoded(body)
            path.write_bytes(data)
            row['request']['bytes'] = len(data)
            row['request']['sha256'] = capture.sha(data)
            contract = capture.production_encoded(manifest['contract'])
            manifest['contractSha256'] = capture.sha(contract)
            raw = capture.production_encoded(manifest)
            (root / 'manifest.json').write_bytes(raw)
            return root, capture.sha(raw), capture.sha(contract)

        with tempfile.TemporaryDirectory() as temp:
            root, manifest_sha, contract_sha = rebuild(temp, lambda body: None)
            with patch.object(capture, 'TRIP_SHA', manifest_sha), patch.object(capture, 'TRIP_CONTRACT', contract_sha):
                self.assertEqual(len(capture.admit_trip_requests(root)), 118)
        for field, value in (('costing', 'pedestrian'), ('shape_match', 'edge_walk')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                root, manifest_sha, contract_sha = rebuild(temp, lambda body: body.update({field: value}))
                with patch.object(capture, 'TRIP_SHA', manifest_sha), patch.object(capture, 'TRIP_CONTRACT', contract_sha):
                    with self.assertRaises(capture.InvalidCapture):
                        capture.admit_trip_requests(root)

    def test_publication_rejects_unbounded_or_unexpected_raw_with_small_failure_receipt(self):
        for filename, data in [('unexpected.log',b'raw internal error'), ('000.response.raw',b'x'*(capture.MAX_RESPONSE+1))]:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp)
                (root/'private/raw').mkdir(parents=True)
                (root/'private/raw'/filename).write_bytes(data)
                result = capture.publish(root/'private', root/'published')
                self.assertEqual(result['status'],'INCOMPLETE')
                self.assertEqual([p.name for p in (root/'published').iterdir()], ['publication.json'])
                self.assertLess((root/'published/publication.json').stat().st_size, 512)


if __name__ == '__main__':
    unittest.main()
