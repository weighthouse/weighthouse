import assert from 'node:assert/strict'
import test from 'node:test'
import {
  currentScroll,
  forgetScroll,
  peekScroll,
  rememberScroll,
  restoreScroll,
} from '../src/scrollMemory.ts'
import { modelHref } from '../src/utils.ts'

// A minimal window/sessionStorage stand-in: the memory module touches
// the browser only through these globals plus rAF. The fake scroll
// clamps to (height - viewport) the way a real document does, so
// restores cannot land before the content is tall enough, and
// drainFrames(1) advances exactly one animation frame.
function installDom({ height = 5000, viewport = 1000 } = {}) {
  let contentHeight = height
  const store = new Map()
  let requested = 0
  let frameQueue = []
  globalThis.sessionStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  }
  const schedule = (callback) => {
    frameQueue.push(callback)
    return frameQueue.length
  }
  globalThis.window = {
    get scrollY() {
      return Math.max(0, Math.min(requested, contentHeight - viewport))
    },
    scrollTo(_x, y) {
      requested = y
    },
    requestAnimationFrame: schedule,
  }
  globalThis.requestAnimationFrame = schedule
  return {
    setHeight(value) {
      contentHeight = value
    },
    drainFrames(times = 1) {
      for (; times > 0; times -= 1) {
        const batch = frameQueue.splice(0)
        for (const callback of batch) callback()
      }
    },
    cleanup() {
      frameQueue = []
      delete globalThis.sessionStorage
      delete globalThis.window
      delete globalThis.requestAnimationFrame
      forgetScroll('probe')
    },
  }
}

test('modelHref encodes owner and name around a literal slash', () => {
  assert.equal(modelHref('acme/tiny'), '/model/acme/tiny')
  assert.equal(modelHref('a b/c d'), '/model/a%20b/c%20d')
  assert.equal(modelHref('meta-llama/Llama-3.1-8B-Instruct'), '/model/meta-llama/Llama-3.1-8B-Instruct')
})

test('modelHref keeps a slash living inside a model name', () => {
  assert.equal(modelHref('owner/sub/dir'), '/model/owner/sub%2Fdir')
})

test('modelHref falls back to the list for malformed ids', () => {
  assert.equal(modelHref(''), '/models')
  assert.equal(modelHref('onlyowner'), '/models')
  assert.equal(modelHref('/'), '/models')
})

test('remember and peek round-trip through the session store', () => {
  const dom = installDom()
  rememberScroll('probe', 410.7)
  assert.equal(peekScroll('probe'), 411)
  dom.cleanup()
})

test('negative offsets clamp to zero, which restore treats as nothing', () => {
  const dom = installDom()
  rememberScroll('probe', -50)
  assert.equal(peekScroll('probe'), 0)
  restoreScroll('probe')
  dom.drainFrames()
  assert.equal(globalThis.window.scrollY, 0)
  dom.cleanup()
})

test('peek tolerates a dead store and never throws', () => {
  const dom = installDom()
  dom.cleanup()
  delete globalThis.sessionStorage
  assert.equal(peekScroll('probe'), null)
  assert.doesNotThrow(() => rememberScroll('probe', 10))
})

test('restore jumps straight to the offset when the page is tall enough', () => {
  const dom = installDom()
  rememberScroll('probe', 2400)
  restoreScroll('probe')
  dom.drainFrames()
  assert.equal(globalThis.window.scrollY, 2400)
  assert.equal(peekScroll('probe'), 2400, 'a landed restore is kept')
  dom.cleanup()
})

test('restore retries while content is late, then lands when it paints', () => {
  const dom = installDom({ height: 1200 })
  rememberScroll('probe', 3000)
  restoreScroll('probe')
  // Frames before the list paints: the document is short, scroll
  // clamps at 200, restore keeps retrying with the entry intact.
  for (let frame = 0; frame < 10; frame += 1) dom.drainFrames()
  assert.notEqual(globalThis.window.scrollY, 3000)
  assert.equal(peekScroll('probe'), 3000, 'pending restores are not dropped yet')
  dom.setHeight(5000)
  dom.drainFrames()
  assert.equal(globalThis.window.scrollY, 3000)
  assert.equal(peekScroll('probe'), 3000)
  dom.cleanup()
})

test('restore forgets entries it never reaches', () => {
  const dom = installDom({ height: 500 })
  rememberScroll('probe', 4000)
  restoreScroll('probe', 3)
  dom.drainFrames(5)
  assert.equal(peekScroll('probe'), null, 'shrunk content drops the entry')
  assert.notEqual(globalThis.window.scrollY, 4000)
  dom.cleanup()
})

test('restore is a no-op with nothing remembered', () => {
  const dom = installDom()
  assert.doesNotThrow(() => restoreScroll('probe'))
  dom.drainFrames()
  dom.cleanup()
})

test('currentScroll reads the live viewport offset', () => {
  const dom = installDom()
  globalThis.window.scrollTo(0, 800)
  assert.equal(currentScroll(), 800)
  dom.cleanup()
})
