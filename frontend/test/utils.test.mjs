import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatBytes,
  formatNumber,
  relativeTime,
  initials,
  taskLabel,
  modelHref,
  formatParameterCount,
} from '../src/utils.ts'

// The formatting family renders the numbers people actually read — the card
// glance line, row columns, and storage figures. These are pure, so the
// exact strings are pinned: a change here changes the UI.

test('formatBytes steps through the units and floors at zero', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(-5), '0 B')
  assert.equal(formatBytes(NaN), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1024), '1.0 KB')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(1024 ** 2), '1.0 MB')
  assert.equal(formatBytes(1024 ** 3), '1.0 GB')
  assert.equal(formatBytes(246.4 * 1024 ** 3), '246.4 GB')
  // Precision is the caller's; byte units never carry a decimal.
  assert.equal(formatBytes(1500, 0), '1 KB')
  assert.equal(formatBytes(12345, 2), '12.06 KB')
})

test('formatNumber goes compact above ten thousand', () => {
  assert.equal(formatNumber(0), '0')
  assert.equal(formatNumber(999), '999')
  assert.equal(formatNumber(9_999), '9,999')
  assert.equal(formatNumber(10_000), '10K')
  assert.equal(formatNumber(26_700), '26.7K')
  assert.equal(formatNumber(1_877), '1,877')
})

test('formatParameterCount speaks the way people say model sizes', () => {
  assert.equal(formatParameterCount(0), '')
  assert.equal(formatParameterCount(-3), '')
  assert.equal(formatParameterCount(NaN), '')
  // Under a million is the exact count.
  assert.equal(formatParameterCount(911_000), '911,000')
  assert.equal(formatParameterCount(1_000_000), '1M')
  assert.equal(formatParameterCount(911_400_000), '911M')
  // Billions: one decimal below ten, whole at ten and up.
  assert.equal(formatParameterCount(1_000_000_000), '1B')
  assert.equal(formatParameterCount(7_115_124_736), '7.1B')
  assert.equal(formatParameterCount(4_100_000_000), '4.1B')
  assert.equal(formatParameterCount(12_000_000_000), '12B')
  assert.equal(formatParameterCount(70_000_000_000), '70B')
})

test('initials take the owner prefix', () => {
  assert.equal(initials('unsloth/Llama-3.3-70B-Instruct-GGUF'), 'UN')
  assert.equal(initials('acme/tiny'), 'AC')
  assert.equal(initials('no-slash'), 'NO')
  assert.equal(initials(''), 'HH')
})

test('taskLabel humanizes the hub task tag', () => {
  assert.equal(taskLabel(null), 'Model')
  assert.equal(taskLabel(''), 'Model')
  assert.equal(taskLabel('text-generation'), 'Text Generation')
  assert.equal(taskLabel('image-text-to-text'), 'Image Text To Text')
})

test('modelHref builds the one address shape for the model page', () => {
  assert.equal(modelHref('acme/tiny'), '/model/acme/tiny')
  assert.equal(modelHref('owner/name/with/slashes'), '/model/owner/name%2Fwith%2Fslashes')
  // Malformed ids never produce a broken route; they fall back to explore.
  assert.equal(modelHref('noslash'), '/models')
  assert.equal(modelHref(''), '/models')
})

test('relativeTime is quiet about the unknown and honest about the past', () => {
  assert.equal(relativeTime(null), 'Unknown')
  assert.equal(relativeTime(''), 'Unknown')
  assert.equal(relativeTime('not-a-date'), 'Unknown')

  const iso = (offsetSeconds) => new Date(Date.now() + offsetSeconds * 1000).toISOString()
  assert.match(relativeTime(iso(-30)), /now|second/i)
  assert.match(relativeTime(iso(-30 * 60)), /30 minutes ago/i)
  assert.match(relativeTime(iso(-5 * 3600)), /5 hours ago/i)
  assert.match(relativeTime(iso(-3 * 86400)), /3 days ago/i)
  // numeric: "auto" renders the singular unit as "last week" / "last year".
  assert.match(relativeTime(iso(-10 * 86400)), /last week|1 week ago/i)
  assert.match(relativeTime(iso(-14 * 86400)), /2 weeks ago/i)
  assert.match(relativeTime(iso(-400 * 86400)), /last year|year ago/i)
  // The future reads as "in …", not a negative count.
  assert.match(relativeTime(iso(3600)), /in 1 hour/i)
})
