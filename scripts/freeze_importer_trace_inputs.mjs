/** Freeze the independently recorded importer-only native trace requests. */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const IMPORTER_INVENTORY_SHA256 = '9ce80de6477a62eb80a2a95a33c5a01a3b1a7ee2ef93873704916ef5156a9a97';
const PREPARATION_SHA256 = 'c25f55abd1e3b07a7d1a5bbb5f6b69b8145db8bd9a5c669720f9787ef9826ebd';
const PRODUCT_SOURCE_SHA256 = {
  'services/valhalla/valhallaTraceMatcher.ts': 'a37da43ece8698cf6c569fd32ba76a178efe4e4a6f4d8c35f9b47269095eabcb',
  'services/import/traceGeometry.ts': '69e515bbd528e11d7ba8ffefbe8ce81aa74b9ec9306ad2f171b535f9f781d6b8',
  'utils/geo.ts': '3ae0c42023bb3014aa61897b8157214dfc1e424963c82751b4ee99e6ad850920',
  'services/valhalla/valhallaConfig.template.json': '9ce5e0c81b4225849ce80fd241b6ba278f69bf172181ee2a7c7a33b3bd72bceb',
};
const ATTRIBUTES_MANIFEST_SHA256 = [
  { id: 'prospective-v1', manifestSha256: '0729b13a8334f964d72d57ac275c4f4082e8ec5f21cb2b41c6a401d6f9e59abc' },
  { id: 'prospective-composite-v1', manifestSha256: 'cba47d133c2d6ff62d95139baca0ed5c5d5295971f9ac486325a10b6aeb6347b' },
];
const EXPECTED_COUNTS = { recordings: 124, exactFrozenMatches: 80, missingNativeInputs: 18, sparsePlans: 26 };
const EXPECTED_REQUESTS = 22;

const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const json = value => JSON.stringify(value);

function read(file, limit = 2 * 1024 * 1024) {
  const stat = fs.lstatSync(file);
  assert(stat.isFile() && stat.size <= limit, 'Input must be one bounded regular file');
  const bytes = fs.readFileSync(file);
  assert(bytes.length <= limit);
  return bytes;
}

function requestFileName(row) {
  return `${row.fixtureId}--${row.variant}--w${String(row.windowIndex).padStart(3, '0')}.trace_attributes.json`;
}

function assertRequest(row, sourcePointCount, selectedPointCount) {
  assert.equal(row.action, 'trace_attributes');
  assert.deepEqual(Object.keys(row.window).sort(), ['endIndex', 'startIndex']);
  assert(Number.isInteger(row.window.startIndex) && Number.isInteger(row.window.endIndex));
  assert(row.window.startIndex >= 0 && row.window.endIndex >= row.window.startIndex);
  assert.equal(row.sourceInputIndices.length, row.window.endIndex - row.window.startIndex + 1);
  assert(row.sourceInputIndices.every((value, index, values) => Number.isInteger(value)
    && value >= 0 && (index === 0 || values[index - 1] < value)));
  assert(Number.isInteger(sourcePointCount) && sourcePointCount > 0);
  assert(Number.isInteger(selectedPointCount) && selectedPointCount > 0);
  assert(selectedPointCount >= row.sourceInputIndices.length);
  assert(row.window.endIndex < selectedPointCount);
  assert(row.sourceInputIndices.at(-1) < sourcePointCount);
  const bytes = Buffer.from(row.request, 'utf8');
  assert(bytes.length <= 8192 && bytes.length === row.bytes && hash(bytes) === row.sha256);
  const request = JSON.parse(bytes.toString('utf8'));
  assert.deepEqual(Object.keys(request).sort(), ['alternates', 'costing', 'filters', 'shape', 'shape_match']);
  assert(Array.isArray(request.shape) && request.shape.length === row.sourceInputIndices.length);
  assert.equal(request.costing, 'auto');
  assert.equal(request.shape_match, 'map_snap');
  return bytes;
}

/**
 * Materialize the 22 unrepresented importer requests without regenerating them.
 * The retained inventory already records exact UTF-8 bytes and source indices;
 * this function refuses any differently serialized, incomplete, or extra input.
 */
export function freeze(inventoryPath, preparationPath, output) {
  assert(!fs.existsSync(output), 'Frozen output is immutable');
  const inventoryBytes = read(inventoryPath);
  const preparationBytes = read(preparationPath, 64 * 1024);
  assert.equal(hash(inventoryBytes), IMPORTER_INVENTORY_SHA256);
  assert.equal(hash(preparationBytes), PREPARATION_SHA256);
  const inventory = JSON.parse(inventoryBytes.toString('utf8'));
  const preparation = JSON.parse(preparationBytes.toString('utf8'));
  assert.deepEqual(inventory.authority.productSourceSha256, PRODUCT_SOURCE_SHA256);
  assert.deepEqual(inventory.authority.attributesManifestSha256, ATTRIBUTES_MANIFEST_SHA256);
  assert.deepEqual(inventory.counts, EXPECTED_COUNTS);
  assert.equal(preparation.importerInventorySha256, IMPORTER_INVENTORY_SHA256);
  assert.deepEqual(preparation.counts, EXPECTED_COUNTS);
  assert.equal(preparation.nativeOutcomesExecuted, false);
  assert.equal(preparation.missingInputAuthorityAdmitted, false);

  const requests = [];
  const names = new Set();
  for (const recording of inventory.recordings) {
    if (recording.disposition !== 'missing-native-input') continue;
    assert.equal(recording.traceable, true);
    assert.equal(recording.variant, 'resampled');
    for (const missing of recording.missingRequests) {
      const row = {
        fixtureId: recording.fixtureId,
        variant: recording.variant,
        split: recording.split,
        sourcePointCount: recording.sourcePointCount,
        selectedPointCount: recording.selectedPointCount,
        action: missing.action,
        windowIndex: missing.windowIndex,
        window: missing.window,
        sourceInputIndices: missing.sourceInputIndices,
        request: undefined,
      };
      const bytes = assertRequest(missing, recording.sourcePointCount, recording.selectedPointCount);
      const file = requestFileName(row);
      assert(!names.has(file), 'Each frozen request must have one unique file');
      names.add(file);
      row.request = { file, bytes: bytes.length, sha256: hash(bytes) };
      requests.push({ row, bytes });
    }
  }
  assert.equal(requests.length, EXPECTED_REQUESTS);
  assert.equal(new Set(requests.map(({ row }) => `${row.fixtureId}|${row.variant}|${row.windowIndex}`)).size,
    EXPECTED_REQUESTS, 'A fixture/window identity may not be duplicated');

  const contract = {
    version: 1,
    authority: {
      importerInventorySha256: IMPORTER_INVENTORY_SHA256,
      preparationSha256: PREPARATION_SHA256,
      productSourceSha256: PRODUCT_SOURCE_SHA256,
      attributesManifestSha256: ATTRIBUTES_MANIFEST_SHA256,
    },
    serialization: 'Exact recorded production JSON.stringify bytes, UTF-8, no trailing newline',
    action: 'trace_attributes',
    requests: requests.map(({ row }) => row),
    nativeOutcomesExecuted: false,
    oracleClassificationAdmitted: false,
    consumerAcceptanceAdmitted: false,
  };
  const manifest = Buffer.from(json({ contractSha256: hash(Buffer.from(json(contract))), contract }));
  assert(manifest.length <= 256 * 1024);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const scratch = fs.mkdtempSync(path.join(path.dirname(output), '.importer-freeze-'));
  try {
    const prepared = path.join(scratch, 'prepared');
    fs.mkdirSync(prepared, { mode: 0o700 });
    for (const { row, bytes } of requests) fs.writeFileSync(path.join(prepared, row.request.file), bytes, { flag: 'wx', mode: 0o600 });
    fs.writeFileSync(path.join(prepared, 'manifest.json'), manifest, { flag: 'wx', mode: 0o600 });
    assert(!fs.existsSync(output));
    fs.renameSync(prepared, output);
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }
  return { requestCount: requests.length, manifestSha256: hash(manifest), contractSha256: hash(Buffer.from(json(contract))) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    assert.equal(process.argv.length, 5);
    console.log(json(freeze(path.resolve(process.argv[2]), path.resolve(process.argv[3]), path.resolve(process.argv[4]))));
  } catch {
    console.error('Importer request freeze refused');
    process.exitCode = 1;
  }
}
