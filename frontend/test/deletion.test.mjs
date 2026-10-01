import assert from 'node:assert/strict'
import test from 'node:test'
import { copyDeletionConfirmed, deletableRows, freedBytesOf } from '../src/deletion.ts'

const plan = {
  schema_version: 1,
  repo_id: 'acme/tiny',
  tracked: [
    { path: 'model.safetensors', size: 100, blob: 'blob-w' },
    { path: 'tokenizer.model', size: 24, blob: 'blob-t' },
  ],
  extras: [{ path: 'leftover.bin', size: 15 }, { path: '.cache', size: 400, kind: 'directory' }],
  whole_copy: { size_bytes: 539, file_count: 4, tracked_manifest: true },
  built_at: '2026-09-26T00:00:00+00:00',
}

test('rows combine tracked records with untracked leftovers', () => {
  const rows = deletableRows(plan)
  assert.deepEqual(
    rows.map((row) => row.path),
    ['model.safetensors', 'tokenizer.model', 'leftover.bin', '.cache'],
  )
})

test('freed bytes sum only the selected rows', () => {
  const picked = deletableRows(plan).filter((row) => ['leftover.bin', '.cache'].includes(row.path))
  assert.equal(freedBytesOf(picked), 415)
  assert.equal(freedBytesOf([]), 0)
})

test('whole-copy confirmation requires the exact repository id', () => {
  assert.equal(copyDeletionConfirmed(plan, ' acme/tiny '), true)
  assert.equal(copyDeletionConfirmed(plan, 'acme/Tiny'), false)
  assert.equal(copyDeletionConfirmed(plan, ''), false)
})
