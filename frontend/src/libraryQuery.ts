// Library query state (D16): the pure half of the chassis. Every choice
// a user can make on the library surface is a value in here, serialized
// to explicit API params — no hidden filter logic anywhere else. The
// facet rail itself is computed by the backend from the actual library;
// favorited and watching are the only built-in filter concepts.

import type { HubModel } from './types'

export const LIBRARY_SORTS = [
  'repo_id',
  'size_bytes',
  'parameter_count',
  'file_count',
  'modified_at',
  'downloaded_at',
  'pipeline_tag',
  'library_name',
  'location',
  'storage_backend',
] as const

export type LibrarySort = (typeof LIBRARY_SORTS)[number]
export type SortOrder = 'asc' | 'desc'

// Explore's "This page" sorts: client-side ordering over the loaded
// batch (the Hub API only orders the whole index, and only those four
// ways). Each key names the row column it mirrors.
export const HUB_PAGE_SORTS = [
  'name',
  'kind',
  'params',
  'quants',
  'library',
  'downloads',
  'likes',
  'modified',
] as const

export type HubPageSort = (typeof HUB_PAGE_SORTS)[number]

function hubPageValue(model: HubModel, sort: HubPageSort): string | number {
  switch (sort) {
    case 'name': return model.id.toLowerCase()
    case 'kind': return (model.pipeline_tag || '').toLowerCase()
    case 'params': return model.parameter_count ?? 0
    case 'quants': return model.quant_count ?? 0
    case 'library': return (model.library_name || '').toLowerCase()
    case 'downloads': return model.downloads ?? 0
    case 'likes': return model.likes ?? 0
    case 'modified': return model.last_modified || ''
  }
}

export function sortHubPage(models: HubModel[], sort: HubPageSort, order: SortOrder): HubModel[] {
  const factor = order === 'asc' ? 1 : -1
  return [...models].sort((a, b) => {
    const av = hubPageValue(a, sort)
    const bv = hubPageValue(b, sort)
    // A given sort key always yields one kind of value, so the string
    // and number branches never mix within a single sort.
    if (typeof av === 'string' && typeof bv === 'string') return factor * av.localeCompare(bv)
    const an = Number(av)
    const bn = Number(bv)
    if (an !== bn) return factor * (an - bn)
    return a.id.localeCompare(b.id)
  })
}
export type Density = 'cards' | 'rows'

export type BooleanFacet = 'favorited' | 'watching'
export type ValueFacet = 'task' | 'library' | 'location' | 'storage'

export interface LibraryQuery {
  query: string
  sort: LibrarySort
  order: SortOrder
  favorited: boolean
  watching: boolean
  task: string
  library: string
  location: string
  storage: string
}

export const defaultLibraryQuery: LibraryQuery = {
  query: '',
  sort: 'modified_at',
  order: 'desc',
  favorited: false,
  watching: false,
  task: '',
  library: '',
  location: '',
  storage: '',
}

// Text columns read better rising; sizes and timestamps read better
// falling. A fresh column sort lands on the direction its data prefers.
const freshOrderFor: Record<LibrarySort, SortOrder> = {
  repo_id: 'asc',
  pipeline_tag: 'asc',
  library_name: 'asc',
  location: 'asc',
  storage_backend: 'asc',
  size_bytes: 'desc',
  parameter_count: 'desc',
  file_count: 'desc',
  modified_at: 'desc',
  downloaded_at: 'desc',
}

export function libraryQuerySearchParams(query: LibraryQuery): URLSearchParams {
  const params = new URLSearchParams()
  if (query.query) params.set('query', query.query)
  if (query.sort !== defaultLibraryQuery.sort) params.set('sort', query.sort)
  if (query.order !== defaultLibraryQuery.order) params.set('order', query.order)
  if (query.favorited) params.set('favorited', 'true')
  if (query.watching) params.set('watching', 'true')
  if (query.task) params.set('task', query.task)
  if (query.library) params.set('library', query.library)
  if (query.location) params.set('location', query.location)
  if (query.storage) params.set('storage', query.storage)
  return params
}

export function toggleBooleanFacet(query: LibraryQuery, key: BooleanFacet): LibraryQuery {
  return { ...query, [key]: !query[key] }
}

export function pickValueFacet(
  query: LibraryQuery,
  key: ValueFacet,
  value: string,
): LibraryQuery {
  const next = value && query[key] !== value ? value : ''
  return { ...query, [key]: next }
}

export function setSort(
  query: LibraryQuery,
  sort: LibrarySort,
  order?: SortOrder,
): LibraryQuery {
  const nextOrder =
    order ?? (query.sort === sort ? (query.order === 'asc' ? 'desc' : 'asc') : freshOrderFor[sort])
  return { ...query, sort, order: nextOrder }
}

export function libraryQuerySignature(query: LibraryQuery): string {
  return libraryQuerySearchParams(query).toString()
}

// A saved view stores exactly this signature; applying it is the parse
// below. Unknown params are ignored, so old views survive new knobs.
export function parseLibraryQuery(search: string | URLSearchParams): LibraryQuery {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search
  const query = { ...defaultLibraryQuery }
  const text = params.get('query')
  if (text !== null) query.query = text
  const sort = params.get('sort') as LibrarySort | null
  if (sort && LIBRARY_SORTS.includes(sort)) query.sort = sort
  const order = params.get('order')
  if (order === 'asc' || order === 'desc') query.order = order
  query.favorited = params.get('favorited') === 'true'
  query.watching = params.get('watching') === 'true'
  for (const key of ['task', 'library', 'location', 'storage'] as const) {
    const value = params.get(key)
    if (value) query[key] = value
  }
  return query
}

export function hasActiveFacets(query: LibraryQuery): boolean {
  return (
    query.favorited
    || query.watching
    || Boolean(query.task)
    || Boolean(query.library)
    || Boolean(query.location)
    || Boolean(query.storage)
  )
}

export function clearFacets(query: LibraryQuery): LibraryQuery {
  return { ...query, favorited: false, watching: false, task: '', library: '', location: '', storage: '' }
}

// Per-user preferences (D15): density is the user's, stored in the
// database, read here. Unknown keys ride along untouched.

export interface SavedView {
  id: string
  name: string
  query: string
}

export interface SavedGroup {
  name: string
  repo_ids: string[]
}

export interface UserPreferences {
  density?: Density
  families?: boolean
  views?: SavedView[]
  groups?: SavedGroup[]
  // Per-surface rows columns, e.g. { local: ['kind','size', ...] }.
  columns?: Record<string, string[]>
  // Registry era the stored column sets were written against. Bump when
  // the column registries change; older stored sets are merged with the
  // current defaults (new default-on columns appear) until the user's
  // next toggle persists the merged set.
  columns_version?: number
  [key: string]: unknown
}

// Bump when any surface's column registry gains columns that default on.
export const COLUMNS_VERSION = 2

// Rows column set for a surface: the stored subset of the full registry,
// in registry order — a column the user enabled that is off by default
// must survive a reload. Unknown stored ids are dropped, so a backend or
// UI version change can never break the grid. No stored set (or an empty
// one) means the surface is at its defaults. A stored set from an older
// registry era shows the union of the stored set and the current
// defaults, so columns added by an upgrade appear without a second login
// or a menu visit.
export function columnsOf(
  preferences: UserPreferences | null | undefined,
  surface: string,
  registry: string[],
  defaults: string[],
): string[] {
  const stored = preferences?.columns?.[surface]
  if (!Array.isArray(stored) || stored.length === 0) return defaults
  if (preferences?.columns_version !== COLUMNS_VERSION) {
    return registry.filter((id) => stored.includes(id) || defaults.includes(id))
  }
  return registry.filter((id) => stored.includes(id))
}

export function withColumns(
  preferences: UserPreferences | null | undefined,
  surface: string,
  ids: string[],
): UserPreferences {
  const base = { ...(preferences || {}) }
  const columns: Record<string, string[]> = {
    ...(base.columns && typeof base.columns === 'object' ? base.columns : {}),
  }
  columns[surface] = ids
  // Writing any column set stamps the registry era, so the one-time
  // default-merge above stops applying to this account.
  return { ...base, columns, columns_version: COLUMNS_VERSION }
}

export function densityOf(preferences: UserPreferences | null | undefined): Density {
  return preferences?.density === 'rows' ? 'rows' : 'cards'
}

export function withDensity(
  preferences: UserPreferences | null | undefined,
  density: Density,
): UserPreferences {
  return { ...(preferences || {}), density }
}

// Family collapse (f-matrix): group the library by repository family —
// the org — under expandable headings. Off by default: the flat matrix
// is the library. "families" avoids colliding with manual *groups*,
// which the user authors themselves.
export function familiesOf(preferences: UserPreferences | null | undefined): boolean {
  return preferences?.families === true
}

export function withFamilies(
  preferences: UserPreferences | null | undefined,
  families: boolean,
): UserPreferences {
  return { ...(preferences || {}), families }
}

// The family name of a repo id is everything before the first slash;
// single-segment ids fall under their own name.
export function familyOf(repoId: string): string {
  const slash = repoId.indexOf('/')
  return slash > 0 ? repoId.slice(0, slash) : repoId
}

// Saved views and manual groups live in the user's preference object.
// Helpers return a new object and carry unknown keys untouched.

export function addView(
  preferences: UserPreferences | null | undefined,
  name: string,
  signature: string,
): UserPreferences {
  const base = { ...(preferences || {}) }
  const views = Array.isArray(base.views) ? [...base.views] : []
  views.push({ id: (crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`).slice(0, 64), name, query: signature })
  return { ...base, views }
}

export function withoutView(
  preferences: UserPreferences | null | undefined,
  id: string,
): UserPreferences {
  const base = { ...(preferences || {}) }
  if (Array.isArray(base.views)) {
    base.views = base.views.filter((view) => view.id !== id)
  }
  return base
}

export function savedViewsOf(
  preferences: UserPreferences | null | undefined,
): SavedView[] {
  const views = preferences?.views
  return Array.isArray(views) ? views.filter((view) => view && typeof view.name === 'string') : []
}

export function addGroup(
  preferences: UserPreferences | null | undefined,
  name: string,
  repoIds: string[],
): UserPreferences {
  const base = { ...(preferences || {}) }
  const groups = Array.isArray(base.groups) ? [...base.groups] : []
  groups.push({ name, repo_ids: [...repoIds] })
  return { ...base, groups }
}

export function withoutGroup(
  preferences: UserPreferences | null | undefined,
  name: string,
): UserPreferences {
  const base = { ...(preferences || {}) }
  if (Array.isArray(base.groups)) {
    base.groups = base.groups.filter((group) => group.name !== name)
  }
  return base
}

export function savedGroupsOf(
  preferences: UserPreferences | null | undefined,
): SavedGroup[] {
  const groups = preferences?.groups
  return Array.isArray(groups)
    ? groups.filter(
        (group) => group && typeof group.name === 'string' && Array.isArray(group.repo_ids),
      )
    : []
}
