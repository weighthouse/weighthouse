import assert from 'node:assert/strict'
import test from 'node:test'
import { downloadErrorSummary, downloadErrorText, sortDownloads } from '../src/downloadPresentation.ts'

function job(id, status, overrides = {}) {
  return { id, repo_id: `acme/${id}`, status, created_at: '2026-01-01', metadata: {}, ...overrides }
}

test('active transfers lead the FIFO queue and restarted jobs join the tail', () => {
  const jobs = [
    job('retry', 'queued', { created_at: '2025-01-01', queued_at: '2026-03-01' }),
    job('second', 'queued', { created_at: '2026-02-01' }),
    job('first', 'queued'),
    job('running', 'downloading'),
    job('paused', 'paused'),
  ]
  assert.deepEqual(sortDownloads(jobs).map(j => j.id), ['running', 'first', 'second', 'retry', 'paused'])
  assert.equal(jobs[0].id, 'retry')
})

test('move-to-front priority matches dispatch order after running transfers', () => {
  const jobs = [job('first', 'queued'), job('front', 'queued', { priority: 3 }), job('running', 'preparing')]
  assert.deepEqual(sortDownloads(jobs).map(j => j.id), ['running', 'front', 'first'])
})

test('retried jobs lead history by completion time for every terminal outcome', () => {
  for (const status of ['failed', 'complete', 'cancelled']) {
    const jobs = [job('older', 'complete', { completed_at: '2026-02-01' }), job('retry', status, { created_at: '2025-01-01', completed_at: '2026-03-01' })]
    assert.deepEqual(sortDownloads(jobs).map(j => j.id), ['retry', 'older'])
  }
})

test('copy includes the full diagnosis, worker output, transport and log path', () => {
  const full = 'HTTP 515: file too large. Use hf_xet.\n' + 'details\n'.repeat(600)
  const error = job('failed', 'failed', { error: 'file too large', metadata: { output_tail: full, engine: 'http', xet_log: '/tmp/report.log' } })
  const text = downloadErrorText(error)
  assert.ok(text.includes(full))
  assert.ok(text.includes('HTTP transport'))
  assert.ok(text.includes('/tmp/report.log'))
  assert.equal(downloadErrorText(job('same', 'failed', { error: 'same diagnostic', metadata: { output_tail: 'same diagnostic' } })).split('same diagnostic').length, 2)
})

test('the compact diagnosis names SIGKILL only when the worker exit code records it', () => {
  assert.equal(downloadErrorSummary(job('killed', 'failed', { metadata: { worker_returncode: -9 } })), 'Process killed (SIGKILL)')
  assert.equal(downloadErrorSummary(job('unknown', 'failed', { error: 'memory pressure' })), 'Error details')
})
