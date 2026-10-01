import assert from 'node:assert/strict'
import test from 'node:test'
import {
  addGroup,
  addView,
  clearFacets,
  columnsOf,
  COLUMNS_VERSION,
  defaultLibraryQuery,
  densityOf,
  familiesOf,
  familyOf,
  hasActiveFacets,
  libraryQuerySearchParams,
  libraryQuerySignature,
  parseLibraryQuery,
  pickValueFacet,
  savedGroupsOf,
  savedViewsOf,
  setSort,
  toggleBooleanFacet,
  withColumns,
  withDensity,
  withFamilies,
  withoutGroup,
  withoutView,
} from '../src/libraryQuery.ts'

test('an all-default query serializes to an empty parameter string', () => {
  assert.equal(libraryQuerySearchParams(defaultLibraryQuery).toString(), '')
})

test('only deviations from the default reach the wire', () => {
  const params = libraryQuerySearchParams(
    toggleBooleanFacet({ ...defaultLibraryQuery, query: 'gguf' }, 'favorited'),
  )
  assert.deepEqual(
    [...params.keys()].sort(),
    ['favorited', 'query'],
  )
  assert.equal(params.get('query'), 'gguf')
})

test('boolean facets toggle on and off', () => {
  const on = toggleBooleanFacet(defaultLibraryQuery, 'watching')
  assert.equal(on.watching, true)
  assert.equal(toggleBooleanFacet(on, 'watching').watching, false)
})

test('a value facet replaces its bucket or toggles off', () => {
  const picked = pickValueFacet(defaultLibraryQuery, 'task', 'text-generation')
  assert.equal(picked.task, 'text-generation')
  const switched = pickValueFacet(picked, 'task', 'image-classification')
  assert.equal(switched.task, 'image-classification')
  assert.equal(pickValueFacet(switched, 'task', 'image-classification').task, '')
  // Bucket independence: picking a library leaves the task alone.
  assert.equal(pickValueFacet(switched, 'library', 'timm').task, 'image-classification')
})

test('re-sorting the same column flips direction, a fresh column takes its own', () => {
  const first = setSort(defaultLibraryQuery, 'size_bytes')
  assert.deepEqual(first, { ...defaultLibraryQuery, sort: 'size_bytes', order: 'desc' })
  const second = setSort(first, 'size_bytes')
  assert.equal(second.order, 'asc')
  const third = setSort(second, 'repo_id')
  assert.equal(third.order, 'asc')
  // An explicit order is honored verbatim.
  assert.equal(setSort(second, 'repo_id', 'desc').order, 'desc')
})

test('facet activity and clearing: the search text survives', () => {
  const active = {
    ...defaultLibraryQuery,
    query: 'gguf',
    favorited: true,
    storage: 's3',
  }
  assert.equal(hasActiveFacets(active), true)
  assert.equal(hasActiveFacets(defaultLibraryQuery), false)
  const cleared = clearFacets(active)
  assert.equal(cleared.query, 'gguf')
  assert.equal(hasActiveFacets(cleared), false)
})

test('signatures differ only when the wire request differs', () => {
  const a = libraryQuerySignature(defaultLibraryQuery)
  const b = libraryQuerySignature({ ...defaultLibraryQuery, query: 'x' })
  assert.notEqual(a, b)
  assert.equal(libraryQuerySignature(clearFacets({ ...defaultLibraryQuery, favorited: true })), a)
})

test('a saved view round-trips through its signature', () => {
  const built = pickValueFacet(
    toggleBooleanFacet(
      setSort({ ...defaultLibraryQuery, query: 'gguf' }, 'size_bytes', 'asc'),
      'favorited',
    ),
    'library',
    'gguf',
  )
  const restored = parseLibraryQuery(libraryQuerySignature(built))
  assert.deepEqual(restored, built)
  // And an all-default view parses back to the default.
  assert.deepEqual(
    parseLibraryQuery(libraryQuerySignature(defaultLibraryQuery)),
    defaultLibraryQuery,
  )
})

test('parse ignores unknown params and bad values instead of erroring', () => {
  const restored = parseLibraryQuery('sort=rank&order=sideways&query=keep&favorited=true')
  assert.equal(restored.sort, defaultLibraryQuery.sort)
  assert.equal(restored.order, defaultLibraryQuery.order)
  assert.equal(restored.query, 'keep')
  assert.equal(restored.favorited, true)
})

test('density reads from preferences with cards as the honest default', () => {
  assert.equal(densityOf(null), 'cards')
  assert.equal(densityOf({}), 'cards')
  assert.equal(densityOf({ density: 'rows' }), 'rows')
})

test('writing density preserves every other preference', () => {
  const before = { density: 'cards', views: [{ id: 'v1', name: 'GGUF', query: 'gguf' }], mystery: 1 }
  const after = withDensity(before, 'rows')
  assert.equal(after.density, 'rows')
  assert.equal(after.mystery, 1)
  assert.deepEqual(after.views, before.views)
  // The original object is not mutated.
  assert.equal(before.density, 'cards')
})

test('views and groups are added, listed, and removed without collateral', () => {
  let prefs = { mystery: 'kept' }
  prefs = addView(prefs, 'GGUF only', 'query=gguf')
  prefs = addView(prefs, 'Big ones', 'sort=size_bytes&order=desc')
  assert.deepEqual(savedViewsOf(prefs).map((view) => view.name), ['GGUF only', 'Big ones'])
  assert.equal(prefs.mystery, 'kept')
  const id = savedViewsOf(prefs)[0].id
  prefs = withoutView(prefs, id)
  assert.deepEqual(savedViewsOf(prefs).map((view) => view.name), ['Big ones'])

  prefs = addGroup(prefs, 'small', ['acme/one', 'acme/two'])
  assert.deepEqual(savedGroupsOf(prefs), [{ name: 'small', repo_ids: ['acme/one', 'acme/two'] }])
  prefs = withoutGroup(prefs, 'small')
  assert.deepEqual(savedGroupsOf(prefs), [])
  assert.equal(prefs.mystery, 'kept')
})

test('families reads from preferences with flat as the honest default', () => {
  assert.equal(familiesOf(null), false)
  assert.equal(familiesOf({}), false)
  assert.equal(familiesOf({ families: true }), true)
  assert.equal(familiesOf({ families: 'yes' }), false)
})

test('writing families preserves every other preference', () => {
  const before = { density: 'rows', mystery: 'kept' }
  const after = withFamilies(before, true)
  assert.equal(after.families, true)
  assert.equal(after.density, 'rows')
  assert.equal(after.mystery, 'kept')
  assert.equal(before.families, undefined)
})

test('a family is the org segment of a repo id', () => {
  assert.equal(familyOf('meta-llama/Llama-3.3-70B-Instruct'), 'meta-llama')
  assert.equal(familyOf('Qwen/CodeQwen1.5-7B-Chat'), 'Qwen')
  assert.equal(familyOf('lonely-name'), 'lonely-name')
  assert.equal(familyOf('/leading'), '/leading')
})

const REGISTRY = ['kind', 'size', 'params', 'location', 'state', 'note', 'checked']
const DEFAULTS = ['kind', 'size', 'location', 'state', 'checked']

test('columnsOf returns the defaults when nothing is stored', () => {
  assert.deepEqual(columnsOf(null, 'watched', REGISTRY, DEFAULTS), DEFAULTS)
  assert.deepEqual(columnsOf({}, 'watched', REGISTRY, DEFAULTS), DEFAULTS)
  assert.deepEqual(columnsOf({ columns: {} }, 'watched', REGISTRY, DEFAULTS), DEFAULTS)
})

test('columnsOf keeps a user-enabled column that is off by default', () => {
  const prefs = {
    columns: { watched: ['kind', 'size', 'location', 'state', 'checked', 'params'] },
    columns_version: COLUMNS_VERSION,
  }
  assert.deepEqual(columnsOf(prefs, 'watched', REGISTRY, DEFAULTS), [
    'kind', 'size', 'params', 'location', 'state', 'checked',
  ])
})

test('columnsOf drops stored ids the registry no longer knows', () => {
  const prefs = { columns: { watched: ['kind', 'ghost', 'note'] }, columns_version: COLUMNS_VERSION }
  assert.deepEqual(columnsOf(prefs, 'watched', REGISTRY, DEFAULTS), ['kind', 'note'])
})

test('columnsOf merges new default-on columns into a stored set from an older era', () => {
  // The registry gained "params", which defaults on. The stored set predates
  // the upgrade (no columns_version); "note" is a column the user enabled.
  const UPGRADED_DEFAULTS = ['kind', 'size', 'params', 'location', 'state', 'checked']
  const prefs = { columns: { watched: ['kind', 'size', 'location', 'state', 'checked', 'note'] } }
  assert.deepEqual(columnsOf(prefs, 'watched', REGISTRY, UPGRADED_DEFAULTS), [
    'kind', 'size', 'params', 'location', 'state', 'note', 'checked',
  ])
  // The user's next toggle persists the merged set and stamps the era,
  // after which the stored list is authoritative again.
  const merged = columnsOf(prefs, 'watched', REGISTRY, UPGRADED_DEFAULTS)
  const stamped = withColumns(prefs, 'watched', merged)
  assert.equal(stamped.columns_version, COLUMNS_VERSION)
  assert.deepEqual(
    columnsOf(stamped, 'watched', REGISTRY, UPGRADED_DEFAULTS),
    ['kind', 'size', 'params', 'location', 'state', 'note', 'checked'],
  )
  // And a deliberate off-stick now holds: turn "size" off, persist, re-read.
  const off = withColumns(stamped, 'watched', merged.filter((id) => id !== 'size'))
  assert.deepEqual(
    columnsOf(off, 'watched', REGISTRY, UPGRADED_DEFAULTS),
    ['kind', 'params', 'location', 'state', 'note', 'checked'],
  )
})

test('columnsOf falls back to defaults when the stored set is empty', () => {
  const prefs = { columns: { watched: [] } }
  assert.deepEqual(columnsOf(prefs, 'watched', REGISTRY, DEFAULTS), DEFAULTS)
})
