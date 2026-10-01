import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from '../src/api.ts'
import { BRAND } from '../src/brand.ts'

// The API client is the one place where the session's CSRF token meets
// every request, and the one place upload resumption is decided. Both are
// security-adjacent, so the rules are pinned against a fetch stub: the
// token rides every write exactly as authStatus/setup/login set it, GETs
// carry none, logout discards it, and a 401 raises the app's unauthorized
// event instead of surfacing as ordinary error copy.

function stubFetch(handler) {
  const calls = []
  globalThis.fetch = async (path, init) => {
    calls.push({ path, init })
    return handler(path, init, calls)
  }
  return calls
}

function ok(payload) {
  return { ok: true, status: 200, json: async () => payload }
}

function fail(status, payload) {
  return { ok: false, status, json: async () => payload }
}

test('the CSRF token rides writes and only writes', async () => {
  const calls = stubFetch(async (path, init) => {
    if (path === '/api/auth/status') {
      return ok({ user: { id: 'u1', username: 'keeper' }, csrf_token: 'tok-123' })
    }
    return ok({})
  })

  await api.authStatus()

  const read = await api.health()
  assert.equal(calls.length, 2)
  assert.deepEqual(read, {}) // payloads pass through untouched
  const readHeaders = new Headers(calls[1].init.headers)
  assert.equal(readHeaders.has('X-CSRF-Token'), false, 'GETs carry no CSRF token')

  await api.checkWatchNow()
  const writeHeaders = new Headers(calls[2].init.headers)
  assert.equal(writeHeaders.get('X-CSRF-Token'), 'tok-123')

  // Logout is itself a write, so it still carries the token; the clear
  // happens after it, so the next write is the one that goes bare.
  await api.logout()
  await api.checkWatchNow()
  const afterLogout = new Headers(calls[4].init.headers)
  assert.equal(afterLogout.has('X-CSRF-Token'), false)
})

test('string bodies get a JSON content type unless one is given', async () => {
  const calls = stubFetch(async () => ok({}))
  await api.savePreferences({ density: 'rows' })
  const headers = new Headers(calls[0].init.headers)
  assert.equal(headers.get('Content-Type'), 'application/json')
  assert.deepEqual(JSON.parse(calls[0].init.body), { preferences: { density: 'rows' } })
})

test('errors surface the backend detail and fall back to the status', async () => {
  stubFetch(async () => fail(409, { detail: 'You already have a collection with that name.' }))
  await assert.rejects(api.createCollection({ name: 'x' }), {
    message: 'You already have a collection with that name.',
  })

  const bare = stubFetch(async () => {
    return { ok: false, status: 500, json: async () => { throw new Error('not json') } }
  })
  await assert.rejects(api.health(), { message: 'Request failed with status 500' })
})

test('a 401 raises the unauthorized event, not just an error', async () => {
  const seen = []
  globalThis.window = { dispatchEvent: (event) => seen.push(event.type) }
  stubFetch(async () => fail(401, { detail: 'Session expired' }))
  await assert.rejects(api.watches(), { message: 'Session expired' })
  assert.deepEqual(seen, [BRAND.unauthorizedEvent])
  delete globalThis.window
})

test('repository ids are encoded per part with the route slash kept', async () => {
  const calls = stubFetch(async () => ok({ id: 'acme/my model' }))
  await api.modelDetails('acme/my model')
  assert.equal(calls[0].path, '/api/hub/models/acme/my%20model')
  await api.unsaveModel('acme/my model')
  assert.equal(calls[1].path, '/api/saved-models/acme/my%20model')
})

test('local model queries are sent as the query parameter', async () => {
  const calls = stubFetch(async () => ok({ items: [], count: 0, total_bytes: 0 }))
  const params = new URLSearchParams()
  params.set('sort', 'name')
  await api.localModels('llama', params)
  assert.equal(calls[0].path, '/api/local-models?sort=name&query=llama')
  await api.localModels()
  assert.equal(calls[1].path, '/api/local-models?')
})

test('a fully uploaded file is not re-sent', async () => {
  const calls = stubFetch(async (path) => {
    if (path.startsWith('/api/uploads/repositories/files/status')) {
      return ok({ offset: 10, complete: true })
    }
    throw new Error('upload should not start')
  })
  const file = new File([Buffer.alloc(10)], 'weights.bin')
  const progress = []
  await api.uploadFile('acme/repo', 'weights.bin', file, 4, (n) => progress.push(n))
  assert.equal(calls.length, 1, 'only the status probe ran')
  assert.deepEqual(progress, [10])
})

test('a conflicting completed or oversized partial file is refused', async () => {
  stubFetch(async (path) => {
    if (path.startsWith('/api/uploads/repositories/files/status')) {
      return ok({ offset: 6, complete: true })
    }
    throw new Error('upload should not start')
  })
  const file = new File([Buffer.alloc(10)], 'weights.bin')
  await assert.rejects(
    api.uploadFile('acme/repo', 'weights.bin', file, 4, () => {}),
    /different completed or partial file already exists/,
  )

  stubFetch(async (path) => {
    if (path.startsWith('/api/uploads/repositories/files/status')) {
      return ok({ offset: 14, complete: false })
    }
    throw new Error('upload should not start')
  })
  await assert.rejects(
    api.uploadFile('acme/repo', 'weights.bin', file, 4, () => {}),
    /different completed or partial file already exists/,
  )
})

test('a partial upload resumes from the recorded offset in chunks', async () => {
  const puts = []
  stubFetch(async (path, init) => {
    if (path.startsWith('/api/uploads/repositories/files/status')) {
      return ok({ offset: 2, complete: false })
    }
    puts.push({
      offset: new Headers(init.headers).get('Upload-Offset'),
      length: new Headers(init.headers).get('Upload-Length'),
      bytes: init.body.size, // chunks are Blobs: size, not length
      type: new Headers(init.headers).get('Content-Type'),
    })
    const next = Number(puts[puts.length - 1].offset) + puts[puts.length - 1].bytes
    return ok({ offset: next })
  })
  const file = new File([Buffer.alloc(10)], 'weights.bin')
  const progress = []
  await api.uploadFile('acme/repo', 'weights.bin', file, 4, (n) => progress.push(n))
  assert.deepEqual(
    puts.map((p) => p.offset),
    ['2', '6'],
  )
  assert.deepEqual(
    puts.map((p) => p.bytes),
    [4, 4],
  )
  assert.equal(puts[0].length, '10')
  assert.equal(puts[0].type, 'application/octet-stream')
  assert.deepEqual(progress, [6, 10])
})
