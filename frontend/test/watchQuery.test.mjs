import assert from 'node:assert/strict'
import test from 'node:test'
import { sortWatchRows, watchLocation } from '../src/watchQuery.ts'

const rows = [
  { repo_id: 'z/last', metadata: { pipeline_tag: 'text-generation' }, copy: { size_bytes: 300, parameter_count: 70, location: 'primary', storage_backend: 'filesystem' }, note_preview: { body: 'Zulu' }, checked_at: '2026-03-01', has_copy: true, update_available: true },
  { repo_id: 'a/first', metadata: { pipeline_tag: 'image-classification' }, copy: { size_bytes: 100, parameter_count: 7, storage_backend: 's3', cached: true }, note_preview: { body: 'Alpha' }, checked_at: '2026-01-01', has_copy: true, update_available: false },
  { repo_id: 'm/unknown', metadata: {}, copy: null, checked_at: null, update_available: false },
]

test('Watched text columns sort in both directions', () => {
  for (const key of ['kind', 'note']) {
    assert.deepEqual(sortWatchRows(rows, key, 'asc').map(r => r.repo_id), ['a/first', 'z/last', 'm/unknown'])
    assert.deepEqual(sortWatchRows(rows, key, 'desc').map(r => r.repo_id), ['z/last', 'a/first', 'm/unknown'])
  }
  assert.deepEqual(sortWatchRows(rows, 'name', 'asc').map(r => r.repo_id), ['a/first', 'm/unknown', 'z/last'])
  assert.deepEqual(sortWatchRows(rows, 'name', 'desc').map(r => r.repo_id), ['z/last', 'm/unknown', 'a/first'])
  assert.deepEqual(sortWatchRows(rows, 'location', 'asc').map(r => r.repo_id), ['z/last', 'a/first', 'm/unknown'])
  assert.deepEqual(sortWatchRows(rows, 'location', 'desc').map(r => r.repo_id), ['a/first', 'z/last', 'm/unknown'])
})

test('Watched numeric and timestamp columns leave unknowns last in both directions', () => {
  for (const key of ['size', 'params', 'checked']) {
    assert.deepEqual(sortWatchRows(rows, key, 'desc').map(r => r.repo_id), ['z/last', 'a/first', 'm/unknown'])
    assert.deepEqual(sortWatchRows(rows, key, 'asc').map(r => r.repo_id), ['a/first', 'z/last', 'm/unknown'])
  }
  assert.equal(rows[0].repo_id, 'z/last')
})

test('Watched State sorting distinguishes changed copies and uses latest check for ties', () => {
  assert.equal(sortWatchRows(rows, 'state', 'desc')[0].repo_id, 'z/last')
  assert.equal(sortWatchRows(rows, 'state', 'asc').at(-1).repo_id, 'z/last')
  assert.equal(watchLocation(null), '')
  assert.equal(watchLocation({ storage_backend: 's3', cached: false }), 'S3 only')
})
