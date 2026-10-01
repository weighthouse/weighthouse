import assert from 'node:assert/strict'
import test from 'node:test'
import {
  UPDATE_STATUS_LABELS,
  applyableUpdateRows,
  reportOnlyUpdateRows,
  transferBytesOf,
  updateSummaryLine,
} from '../src/updateDiff.ts'

function diffWith(files) {
  const summary = { changed: 0, added: 0, missing: 0, removed: 0, unchanged: 0 }
  for (const [, status] of files) summary[status] += 1
  return {
    schema_version: 1,
    repo_id: 'acme/tiny',
    requested_revision: 'main',
    local: { revision: 'main', sha: 'sha-old', status: 'complete' },
    upstream: { sha: 'sha-new', fetched_at: '2026-09-26T00:00:00+00:00' },
    updates_available: files.length > 0,
    results_in: { transfer_bytes: 0, changed: 0, added: 0, missing: 0 },
    summary,
    added_reporting_limited: false,
    files: files.map(([path, status, size]) => ({
      path,
      status,
      local: { size: status === 'added' ? 0 : size, blob: 'blob-old' },
      upstream: { size, blob: 'blob-new' },
      size_delta: status === 'removed' ? 0 : size,
    })),
  }
}

test('applyable rows include changed, added, and missing files only', () => {
  const diff = diffWith([
    ['weights.safetensors', 'changed', 500],
    ['new.json', 'added', 2],
    ['gone.bin', 'missing', 300],
    ['drop.bin', 'removed', 999],
    ['same.txt', 'unchanged', 50],
  ])

  const applyable = applyableUpdateRows(diff).map((row) => row.path)
  assert.deepEqual(applyable, ['weights.safetensors', 'new.json', 'gone.bin'])

  const reportOnly = reportOnlyUpdateRows(diff).map((row) => row.path)
  assert.deepEqual(reportOnly, ['drop.bin'])
})

test('transfer bytes sum only the rows presented', () => {
  const rows = applyableUpdateRows(
    diffWith([['a.safetensors', 'changed', 500], ['b.safetensors', 'missing', 300]]),
  )
  assert.equal(transferBytesOf(rows), 800)
  assert.equal(transferBytesOf([]), 0)
})

test('summary line lists only populated groups', () => {
  assert.equal(
    updateSummaryLine(diffWith([['a', 'changed', 1], ['b', 'added', 2], ['c', 'removed', 3]])),
    '1 changed · 1 new · 1 removed',
  )
  assert.equal(updateSummaryLine(diffWith([['a', 'unchanged', 1]])), '')
})

test('status labels stay in sync with the page rendering', () => {
  assert.equal(UPDATE_STATUS_LABELS.missing, 'Needs re-fetch')
  assert.equal(UPDATE_STATUS_LABELS.removed, 'Removed upstream')
})
