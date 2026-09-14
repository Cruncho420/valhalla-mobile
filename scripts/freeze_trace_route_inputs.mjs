// Source-only preparation: execute pinned serializers, never a native actor or matcher outcome.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pins = {
  'services/valhalla/valhallaTraceMatcher.ts': 'a37da43ece8698cf6c569fd32ba76a178efe4e4a6f4d8c35f9b47269095eabcb',
  'services/import/traceGeometry.ts': '69e515bbd528e11d7ba8ffefbe8ce81aa74b9ec9306ad2f171b535f9f781d6b8',
  'utils/geo.ts': '3ae0c42023bb3014aa61897b8157214dfc1e424963c82751b4ee99e6ad850920',
  'services/valhalla/valhallaConfig.template.json': '9ce5e0c81b4225849ce80fd241b6ba278f69bf172181ee2a7c7a33b3bd72bceb',
};
const groups = [
  ['prospective-v1', '0729b13a8334f964d72d57ac275c4f4082e8ec5f21cb2b41c6a401d6f9e59abc', 110],
  ['prospective-composite-v1', 'cba47d133c2d6ff62d95139baca0ed5c5d5295971f9ac486325a10b6aeb6347b', 33],
];
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const json = value => JSON.stringify(value); // The production serializer uses this exact encoding.
function read(file, max = 1048576) {
  const stat = fs.lstatSync(file);
  assert(stat.isFile() && stat.size <= max);
  const bytes = fs.readFileSync(file);
  assert(bytes.length <= max);
  return bytes;
}

export async function prepare(app, output) {
  assert(!fs.existsSync(output), 'Existing preparation is immutable');
  const sources = Object.fromEntries(Object.entries(pins).map(([name, sha]) => {
    const bytes = read(path.join(app, name));
    assert.equal(hash(bytes), sha, 'Product source differs from reviewed preparation authority');
    return [name, bytes.toString('utf8')];
  }));
  const appRequire = createRequire(path.join(app, 'package.json'));
  const ts = appRequire('typescript');
  const compilerPath = appRequire.resolve('typescript');
  const transportStop = new Error('Source-only request capture; native execution forbidden');
  let observedTrip;
  function compile(name, requireImpl, expose = '') {
    const module = {exports: {}};
    const javascript = ts.transpileModule(sources[name], {compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
    }}).outputText;
    new Function('require', 'module', 'exports', javascript + expose)(requireImpl, module, module.exports);
    return module.exports;
  }
  const rejectImport = () => { throw new Error('Unexpected source dependency'); };
  const geo = compile('utils/geo.ts', rejectImport);
  const traceGeometry = compile('services/import/traceGeometry.ts', name => {
    if (name === '../../utils/geo') return geo;
    return rejectImport();
  });
  const matcher = compile('services/valhalla/valhallaTraceMatcher.ts', name => {
    if (name === '../../utils/geo') return geo;
    if (name === '../errorReporting/sentryService') return {addBreadcrumb() {}, captureError() {}};
    if (name === './valhallaConfig.template.json') return JSON.parse(sources['services/valhalla/valhallaConfig.template.json']);
    if (name === './valhallaActorOwner') return {valhallaActorScheduler: {run: (_kind, work) => work()}};
    if (name === './valhallaWorkScheduler') return {ValhallaWorkError: class extends Error {}};
    if (name === '../../modules/rods-routing/src') return {
      hasNativeTrace: () => true,
      traceRoute: request => { assert.equal(observedTrip, undefined); observedTrip = request; throw transportStop; },
      traceAttributes: rejectImport, parseNativeTraceAttributes: rejectImport,
    };
    return rejectImport();
  }, '\nmodule.exports.sourceOnlyBuildAttributesRequest = buildRequest;');
  // The extra export exposes the actual private serializer in memory only; app source is untouched.
  const recordings = [];
  for (const [group, manifestSha, count] of groups) {
    const root = path.join(repo, 'test-fixtures', group);
    const manifestBytes = read(path.join(root, 'manifest.json'));
    assert.equal(hash(manifestBytes), manifestSha);
    const rows = JSON.parse(manifestBytes).contract.requests;
    assert.equal(rows.length, count);
    const expectedFiles = new Set(['manifest.json']);
    const byRecording = new Map();
    for (const row of rows) {
      for (const kind of ['original', 'diagnostic']) {
        const file = row[kind];
        assert.match(file.file, /^[a-z0-9-]+\.(original|diagnostic)\.json$/);
        assert(!expectedFiles.has(file.file)); expectedFiles.add(file.file);
        const bytes = read(path.join(root, file.file), 8192);
        assert.equal(bytes.length, file.bytes); assert.equal(hash(bytes), file.sha256);
      }
      const key = `${row.fixtureId}|${row.variant}`;
      if (!byRecording.has(key)) byRecording.set(key, []);
      byRecording.get(key).push(row);
    }
    assert.deepEqual(fs.readdirSync(root).sort(), [...expectedFiles].sort());
    for (const rows of byRecording.values()) {
      const samples = [];
      for (const row of rows) {
        const points = JSON.parse(read(path.join(root, row.original.file), 8192)).shape;
        assert.equal(points.length, row.window.endIndex - row.window.startIndex + 1);
        points.forEach((point, index) => {
          const position = row.window.startIndex + index;
          const sample = {latitude: point.lat, longitude: point.lon};
          if (samples[position]) assert.deepEqual(samples[position], sample);
          samples[position] = sample;
        });
      }
      assert.equal(Object.keys(samples).length, samples.length);
      assert.deepEqual(matcher.planTraceWindows(samples), rows.map(row => row.window));
      recordings.push({group, rows, samples});
    }
  }
  assert.equal(recordings.length, 124);
  const files = new Map();
  const trips = [], excluded = [], importer = [];
  for (const {group, rows, samples} of recordings) {
    const {fixtureId, variant, split} = rows[0];
    const identity = {group, fixtureId, variant, split};
    const sourceInputIndices = samples.map((_, index) => index);
    if (rows.length === 1) {
      observedTrip = undefined;
      await assert.rejects(matcher.traceWholeRecordingRoute(samples), error => error === transportStop);
      assert.equal(typeof observedTrip, 'string');
      const bytes = Buffer.from(observedTrip, 'utf8');
      assert(bytes.length <= 8192);
      const file = `${fixtureId}--${variant}.trace_route.json`;
      assert(!files.has(file)); files.set(file, bytes);
      trips.push({...identity, action: 'trace_route', windowIndex: 0,
        window: {startIndex: 0, endIndex: samples.length - 1}, sourceInputIndices,
        ...(rows[0].originalSourceIndices ? {originalSourceIndices: rows[0].originalSourceIndices} : {}),
        request: {file, bytes: bytes.length, sha256: hash(bytes)}});
    } else {
      excluded.push({...identity, windows: rows.length,
        reason: 'MULTI_WINDOW_ADMITTED=false; no whole-trip call through current facade'});
    }
    const shape = traceGeometry.measureTraceShape(samples);
    const selected = shape.isTraceable ? traceGeometry.resampleForMatching(samples, shape).points : null;
    const matches = selected ? recordings.filter(other => other.rows[0].fixtureId === fixtureId
        && json(other.samples) === json(selected)).map(other => ({group: other.group,
          fixtureId, variant: other.rows[0].variant, windows: other.rows.length})) : [];
    // Reference identity, not nearest-coordinate guessing: duplicate positions retain their index.
    const originalIndices = new Map(samples.map((sample, index) => [sample, index]));
    const selectedIndices = selected?.map(sample => { assert(originalIndices.has(sample)); return originalIndices.get(sample); });
    const missingRequests = selected && !matches.length ? matcher.planTraceWindows(selected).map((window, windowIndex) => {
      const request = matcher.sourceOnlyBuildAttributesRequest(selected, window);
      return {windowIndex, window, action: 'trace_attributes', bytes: Buffer.byteLength(request),
        sha256: hash(request), request, sourceInputIndices: selectedIndices.slice(window.startIndex, window.endIndex + 1)};
    }) : [];
    importer.push({...identity, sourcePointCount: samples.length, sourceCoordinatesSha256: hash(json(samples)),
      traceable: shape.isTraceable, selectedPointCount: selected?.length ?? 0,
      selectedCoordinatesSha256: selected ? hash(json(selected)) : null, selectedSourceIndices: selectedIndices ?? [],
      unchanged: selected ? json(samples) === json(selected) : false, frozenMatches: matches,
      disposition: !selected ? 'sparse-plan' : matches.length ? 'exact-frozen-match' : 'missing-native-input',
      missingRequests});
  }
  assert.equal(trips.length, 118); assert.equal(excluded.length, 6);
  const counts = {recordings: importer.length,
    exactFrozenMatches: importer.filter(row => row.disposition === 'exact-frozen-match').length,
    missingNativeInputs: importer.filter(row => row.disposition === 'missing-native-input').length,
    sparsePlans: importer.filter(row => row.disposition === 'sparse-plan').length};
  assert.deepEqual(counts, {recordings: 124, exactFrozenMatches: 80, missingNativeInputs: 18, sparsePlans: 26});
  const authority = {productSourceSha256: pins, generatorSha256: hash(read(fileURLToPath(import.meta.url))),
    compiler: {version: ts.version, sha256: hash(read(compilerPath, 16 * 1048576))},
    attributesManifestSha256: groups.map(([id, manifestSha256]) => ({id, manifestSha256}))};
  const contract = {version: 1, authority, serialization: 'Exact production JSON.stringify bytes, UTF-8, no trailing newline',
    action: 'trace_route', requests: trips, excludedRecordings: excluded,
    nativeOutcomesExecuted: false, oracleClassificationAdmitted: false, consumerAcceptanceAdmitted: false};
  const inventory = {version: 1, authority, counts,
    parameters: {minTracePoints: traceGeometry.MIN_TRACE_POINTS, maxTraceMedianSpacingMeters: traceGeometry.MAX_TRACE_MEDIAN_SPACING_M,
      targetSpacingMeters: traceGeometry.MATCH_TARGET_SPACING_M, maxMatchPoints: traceGeometry.MAX_MATCH_POINTS},
    recordings: importer, nativeOutcomesExecuted: false, captureGroupsModified: false,
    missingInputAuthorityAdmitted: false, consumerAcceptanceAdmitted: false};
  const inventoryBytes = Buffer.from(json(inventory));
  assert(inventoryBytes.length < 2 * 1048576);
  const manifest = Buffer.from(json({contractSha256: hash(json(contract)), contract}));
  assert(manifest.length < 262144);
  // Publication happens only after every input, source, count, and selection check passed.
  fs.mkdirSync(path.dirname(output), {recursive: true});
  const privateDir = fs.mkdtempSync(path.join(path.dirname(output), '.trip-source-'));
  try {
    const prepared = path.join(privateDir, 'prepared'); fs.mkdirSync(prepared, {mode: 0o700});
    const requests = path.join(prepared, 'requests'); fs.mkdirSync(requests, {mode: 0o700});
    for (const [name, bytes] of files) fs.writeFileSync(path.join(requests, name), bytes, {flag: 'wx', mode: 0o600});
    fs.writeFileSync(path.join(requests, 'manifest.json'), manifest, {flag: 'wx', mode: 0o600});
    fs.writeFileSync(path.join(prepared, 'importer-inventory.json'), inventoryBytes, {flag: 'wx', mode: 0o600});
    fs.writeFileSync(path.join(prepared, 'preparation.json'), json({version: 1, requestCount: 118,
      requestManifestSha256: hash(manifest), importerInventorySha256: hash(inventoryBytes), counts,
      nativeOutcomesExecuted: false, missingInputAuthorityAdmitted: false}), {flag: 'wx', mode: 0o600});
    assert(!fs.existsSync(output)); fs.renameSync(prepared, output);
  } finally { fs.rmSync(privateDir, {recursive: true}); }
  return {requests: 118, counts, manifestSha256: hash(manifest), importerInventorySha256: hash(inventoryBytes)};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    assert.equal(process.argv.length, 4);
    console.log(json(await prepare(path.resolve(process.argv[2]), path.resolve(process.argv[3]))));
  } catch {
    console.error('Source-only trace request preparation refused'); process.exitCode = 1;
  }
}
