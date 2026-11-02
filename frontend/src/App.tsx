import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useScrollMemory } from './useScrollMemory'
import {
  AlertCircle,
  ArrowDownToLine,
  ArrowUp,
  Ban,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  CircleX,
  Clock3,
  Cloud,
  Download,
  Filter,
  HardDrive,
  KeyRound,
  LayoutGrid,
  List,
  ListFilter,
  LoaderCircle,
  PackageCheck,
  Pause,
  Play,
  RefreshCw,
  RotateCw,
  Search,
  Server,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import {
  HashRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from 'react-router-dom'
import { api } from './api'
import { downloadErrorSummary, downloadErrorText, sortDownloads } from './downloadPresentation'
import { BRAND } from './brand.ts'
import {
  addGroup,
  addView,
  clearFacets,
  columnsOf,
  defaultLibraryQuery,
  densityOf,
  familiesOf,
  familyOf,
  HUB_PAGE_SORTS,
  libraryQuerySearchParams,
  libraryQuerySignature,
  parseLibraryQuery,
  pickValueFacet,
  savedGroupsOf,
  savedViewsOf,
  setSort,
  sortHubPage,
  toggleBooleanFacet,
  withColumns,
  withFamilies,
  LIBRARY_SORTS,
  withDensity,
  withoutGroup,
  withoutView,
  type Density,
  type HubPageSort,
  type LibraryQuery,
  type LibrarySort,
  type SavedGroup,
  type SavedView,
  type UserPreferences,
} from './libraryQuery.ts'
import { AccountAdmin, AgentKeysAdmin, AuthScreen, SavedPage, UploadsPage } from './components/AccountPages'
import { BrandMark } from './components/BrandMarks'
import { ModelPage } from './components/ModelPage'
import { LibraryRail } from './components/LibraryRail'
import {
  ColumnsMenu,
  HUB_COLUMNS,
  HUB_COLUMNS_DEFAULT,
  HubModelRow,
  HubModelRowFull,
  LOCAL_COLUMNS,
  LOCAL_COLUMNS_DEFAULT,
  LocalMatrixCard,
  LocalModelRow,
  RowsHead,
  rowsGridTemplate,
  HUB_COLUMN_IDS,
  LOCAL_COLUMN_IDS,
} from './components/RepositoryRows'
import Shell from './components/Shell'
import { WatchedPage } from './components/WatchedPage'
import type {
  AuthStatus,
  DownloadJob,
  Health,
  HubModel,
  LibraryFacets,
  LocalModel,
  RuntimeJob,
  RuntimeTarget,
  User,
  WatchList,
  WatchRecord,
} from './types'
import { formatBytes, modelHref, relativeTime } from './utils'

type ToastTone = 'success' | 'error'
type ToastHandler = (message: string, tone?: ToastTone) => void

const taskOptions = [
  ['text-generation', 'Text Generation'],
  ['image-text-to-text', 'Image-Text-to-Text'],
  ['text-to-image', 'Text-to-Image'],
  ['feature-extraction', 'Embeddings'],
  ['automatic-speech-recognition', 'Speech Recognition'],
]

// Human labels for the whitelisted sort columns.
const columnLabels: Record<LibrarySort, string> = {
  repo_id: 'Name',
  size_bytes: 'Size',
  parameter_count: 'Params',
  file_count: 'Files',
  modified_at: 'Modified',
  downloaded_at: 'Downloaded',
  pipeline_tag: 'Task',
  library_name: 'Library',
  location: 'Location',
  storage_backend: 'Storage',
  state: 'State',
}
const libraryOptions = [
  ['transformers', 'Transformers'],
  ['gguf', 'GGUF'],
  ['diffusers', 'Diffusers'],
  ['safetensors', 'Safetensors'],
]
const parameterOptions = [
  ['max:1B', '< 1B'],
  ['min:1B,max:7B', '1B – 7B'],
  ['min:7B,max:32B', '7B – 32B'],
  ['min:32B,max:128B', '32B – 128B'],
  ['min:128B', '> 128B'],
]

function FilterGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string
  options: string[][]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <section className="filter-group">
      <h3>{title}</h3>
      {options.map(([id, label]) => (
        <button
          type="button"
          key={id}
          className={value === id ? 'selected' : ''}
          onClick={() => onChange(value === id ? '' : id)}
        >
          <span className="filter-check">{value === id && <Check size={12} />}</span>
          {label}
        </button>
      ))}
    </section>
  )
}

const SORT_LABELS: Record<string, string> = {
  trending: 'trending',
  downloads: 'downloads',
  updated: 'recently updated',
  likes: 'likes',
}

// Meta-line words for the client-side "This page" sorts.
const PAGE_SORT_LABELS: Record<HubPageSort, string> = {
  name: 'name',
  kind: 'kind',
  params: 'parameters',
  quants: 'quants',
  library: 'library',
  downloads: 'downloads',
  likes: 'likes',
  modified: 'modified',
  state: 'state',
}

function ModelsPage({
  onToast,
  refreshDownloads,
  user,
}: {
  onToast: ToastHandler
  refreshDownloads: () => void
  user: User
}) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [search, setSearch] = useState(searchParams.get('search') || '')
  const [task, setTask] = useState('')
  const [library, setLibrary] = useState('')
  const [parameters, setParameters] = useState('')
  const [sort, setSort] = useState('trending')
  // "This page" sorts reorder the loaded batch client-side; the Hub
  // sorts order the whole index server-side. One is active at a time.
  const [pageSort, setPageSort] = useState<HubPageSort | ''>('')
  const [pageOrder, setPageOrder] = useState<'asc' | 'desc'>('asc')
  const [models, setModels] = useState<HubModel[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const [cardQueuing, setCardQueuing] = useState<string | null>(null)
  // The cards/rows preference is the user's representation choice everywhere
  // models render; Explore reads and writes the same value as the Library.
  const [preferences, setPreferences] = useState<UserPreferences>({})
  const density = densityOf(preferences)
  const preferencesRef = useRef(preferences)
  useEffect(() => {
    preferencesRef.current = preferences
  }, [preferences])
  const visibleHubColumns = columnsOf(preferences, 'hub', HUB_COLUMN_IDS, HUB_COLUMNS_DEFAULT)
  // Explore data paints a beat after mount (search debounce, network), so
  // the saved offset is honored by restore's frame retries, not eagerly.
  useScrollMemory('models')
  const openModel = useCallback(
    (repoId: string) => navigate(modelHref(repoId)),
    [navigate],
  )
  const openModelUpdates = useCallback(
    (repoId: string) => navigate(`${modelHref(repoId)}?tab=updates`),
    [navigate],
  )

  // The card action queues the full repository in place (main revision,
  // watch on); the download zone on the page stays the door for custom
  // selections.
  async function queueCardDownload(repoId: string) {
    if (cardQueuing) return
    setCardQueuing(repoId)
    try {
      await api.startDownload({ repo_id: repoId, mode: 'full', watch: true })
      onToast(`${repoId} was queued (full repository).`)
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to queue the download', 'error')
    } finally {
      setCardQueuing(null)
    }
  }

  useEffect(() => {
    api
      .preferences()
      .then((payload) => setPreferences(payload.preferences))
      .catch(() => undefined)
  }, [])

  async function changeDensity(next: Density) {
    const current = preferencesRef.current
    const optimistic = withDensity(current, next)
    preferencesRef.current = optimistic
    setPreferences(optimistic)
    try {
      const stored = await api.savePreferences(optimistic)
      preferencesRef.current = stored.preferences
      setPreferences(stored.preferences)
    } catch (reason) {
      preferencesRef.current = current
      setPreferences(current)
      onToast(reason instanceof Error ? reason.message : 'Could not save the display preference.', 'error')
    }
  }

  async function toggleHubColumn(id: string) {
    const current = preferencesRef.current
    const keep = new Set(columnsOf(current, 'hub', HUB_COLUMN_IDS, HUB_COLUMNS_DEFAULT))
    if (keep.has(id)) keep.delete(id)
    else keep.add(id)
    const next = HUB_COLUMNS.map((column) => column.id).filter((columnId) => keep.has(columnId))
    const optimistic = withColumns(current, 'hub', next)
    preferencesRef.current = optimistic
    setPreferences(optimistic)
    try {
      const stored = await api.savePreferences(optimistic)
      preferencesRef.current = stored.preferences
      setPreferences(stored.preferences)
    } catch (reason) {
      preferencesRef.current = current
      setPreferences(current)
      onToast(reason instanceof Error ? reason.message : 'Could not save the column preference.', 'error')
    }
  }

  useEffect(() => {
    const next = searchParams.get('search') || ''
    setSearch(next)
  }, [searchParams])

  const fetchModels = useCallback(() => {
    const params = new URLSearchParams({
      search,
      sort,
      limit: '30',
    })
    if (task) params.set('task', task)
    if (library) params.set('library', library)
    if (parameters) params.set('parameters', parameters)
    setLoading(true)
    setError('')
    api
      .searchModels(params)
      .then((payload) => setModels(payload.items))
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false))
  }, [library, parameters, search, sort, task])

  useEffect(() => {
    const timer = window.setTimeout(fetchModels, 350)
    return () => window.clearTimeout(timer)
  }, [fetchModels])

  const displayedModels = useMemo(
    () => (pageSort ? sortHubPage(models, pageSort, pageOrder) : models),
    [models, pageSort, pageOrder],
  )
  const freshPageOrder: Record<HubPageSort, 'asc' | 'desc'> = {
    name: 'asc',
    kind: 'asc',
    params: 'desc',
    quants: 'desc',
    library: 'asc',
    downloads: 'desc',
    likes: 'desc',
    modified: 'desc',
    state: 'asc',
  }
  function choosePageSort(key: string) {
    if (!(HUB_PAGE_SORTS as readonly string[]).includes(key)) return
    const next = key as HubPageSort
    if (pageSort === next) {
      setPageOrder((order) => (order === 'asc' ? 'desc' : 'asc'))
    } else {
      setPageSort(next)
      setPageOrder(freshPageOrder[next])
    }
  }

  const activeFilters = [task, library, parameters].filter(Boolean).length

  async function toggleSaved(model: HubModel) {
    setSaving(model.id)
    try {
      if (model.saved) {
        await api.unsaveModel(model.id)
        onToast(`${model.id} was removed from your saved library.`)
      } else {
        await api.saveModel({
          repo_id: model.id,
          metadata: {
            author: model.author,
            pipeline_tag: model.pipeline_tag,
            library_name: model.library_name,
            license: model.license,
            parameter_count: model.parameter_count,
            last_modified: model.last_modified,
            local: model.local,
          },
        })
        onToast(`${model.id} was saved for later.`)
      }
      setModels((current) =>
        current.map((item) =>
          item.id === model.id ? { ...item, saved: !model.saved } : item,
        ),
      )
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to update saved models', 'error')
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="explore-layout">
      <div className="page-heading">
        <div>
          <h1>Explore models</h1>
          <p className="meta-line">
            {loading ? (
              'Contacting the Hub…'
            ) : (
              <>
                {models.length} result{models.length === 1 ? '' : 's'}
                {search ? <> for "{search}"</> : null}
                <span className="sep">/</span>
                sorted by {pageSort ? PAGE_SORT_LABELS[pageSort] : SORT_LABELS[sort]}
              </>
            )}
          </p>
        </div>
        <div className="heading-actions">
          <label className="sort-control">
            <SlidersHorizontal size={15} />
            <select
              value={pageSort ? `page:${pageSort}` : sort}
              onChange={(event) => {
                const value = event.target.value
                if (value.startsWith('page:')) {
                  choosePageSort(value.slice(5))
                } else {
                  setPageSort('')
                  setSort(value)
                }
              }}
              aria-label="Sort models"
            >
              <optgroup label="Hugging Face">
                <option value="trending">Trending</option>
                <option value="downloads">Most downloaded</option>
                <option value="updated">Recently updated</option>
                <option value="likes">Most liked</option>
              </optgroup>
              <optgroup label="This page">
                <option value="page:name">Name</option>
                <option value="page:kind">Kind</option>
                <option value="page:params">Parameters</option>
                <option value="page:quants">Quants</option>
                <option value="page:library">Library</option>
                <option value="page:downloads">Downloads</option>
                <option value="page:likes">Likes</option>
                <option value="page:modified">Modified</option>
                <option value="page:state">State</option>
              </optgroup>
            </select>
            <ChevronDown size={14} />
          </label>
          <div className="density-toggle" role="group" aria-label="Display density">
            <button
              type="button"
              className={density === 'cards' ? 'on' : ''}
              aria-pressed={density === 'cards'}
              onClick={() => changeDensity('cards')}
            >
              <LayoutGrid size={14} /> Cards
            </button>
            <button
              type="button"
              className={density === 'rows' ? 'on' : ''}
              aria-pressed={density === 'rows'}
              onClick={() => changeDensity('rows')}
            >
              <List size={14} /> Rows
            </button>
          </div>
          {density === 'rows' && (
            <ColumnsMenu
              spec={HUB_COLUMNS}
              visible={visibleHubColumns}
              onToggle={toggleHubColumn}
            />
          )}
          <button
            type="button"
            className="secondary-button mobile-filter-button"
            onClick={() => setMobileFiltersOpen(true)}
          >
            <ListFilter size={15} />
            Filters {activeFilters > 0 ? `(${activeFilters})` : ''}
          </button>
        </div>
      </div>

      <div className="catalog-layout explore-rail">
        <aside className={mobileFiltersOpen ? 'filters mobile-open' : 'filters'}>
          <div className="filters-heading">
            <Filter size={16} />
            <span>Models</span>
            {activeFilters > 0 && <em>{activeFilters}</em>}
            <button
              type="button"
              className="filter-mobile-close"
              onClick={() => setMobileFiltersOpen(false)}
              aria-label="Close filters"
            >
              <CircleX size={18} />
            </button>
          </div>
          <FilterGroup title="Tasks" options={taskOptions} value={task} onChange={setTask} />
          <FilterGroup
            title="Libraries & formats"
            options={libraryOptions}
            value={library}
            onChange={setLibrary}
          />
          <FilterGroup
            title="Parameters"
            options={parameterOptions}
            value={parameters}
            onChange={setParameters}
          />
          {activeFilters > 0 && (
            <button
              className="clear-filters"
              onClick={() => {
                setTask('')
                setLibrary('')
                setParameters('')
              }}
            >
              Reset filters
            </button>
          )}
        </aside>

        <section className="catalog-content">
          {error && (
            <div className="page-error">
              <AlertCircle size={18} />
              <div>
                <strong>Could not reach Hugging Face</strong>
                <p>{error}</p>
              </div>
              <button onClick={fetchModels}>Retry</button>
            </div>
          )}
          {loading ? (
            <div className="matrix-grid skeleton-card-grid" aria-label="Loading models">
              {Array.from({ length: 8 }).map((_, index) => (
                <div key={index} className="matrix-card skeleton-card" aria-hidden="true">
                  <div className="skeleton line-strong" />
                  <div className="skeleton line-medium" />
                  <div className="skeleton line-short" />
                </div>
              ))}
            </div>
          ) : density === 'rows' ? (
            <div
              className="rows-view"
              style={{ '--rows-grid': rowsGridTemplate(HUB_COLUMNS, visibleHubColumns) } as CSSProperties}
            >
              <RowsHead
                spec={HUB_COLUMNS}
                visible={visibleHubColumns}
                sortColumn={pageSort || null}
                order={pageOrder}
                onSort={choosePageSort}
              />
              {displayedModels.map((model) => (
                <HubModelRowFull key={model.id} model={model} onOpen={openModel} visible={visibleHubColumns} />
              ))}
              {!error && models.length === 0 && (
                <div className="empty-state">
                  <Box size={30} />
                  <h2>No matching models</h2>
                  <p>Clear a filter or try a broader repository name.</p>
                </div>
              )}
            </div>
          ) : (
            <div className="matrix-grid">
              {displayedModels.map((model) => (
                <HubModelRow
                  key={model.id}
                  model={model}
                  onOpen={openModel}
                  onDownload={() => (model.local ? openModelUpdates(model.id) : queueCardDownload(model.id))}
                  onSave={toggleSaved}
                  saving={saving === model.id}
                  queuing={cardQueuing === model.id}
                />
              ))}
              {!error && models.length === 0 && (
                <div className="empty-state">
                  <Box size={30} />
                  <h2>No matching models</h2>
                  <p>Clear a filter or try a broader repository name.</p>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function LocalPage({ onToast, user }: { onToast: ToastHandler; user: User }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [health, setHealth] = useState<Health | null>(null)
  const [models, setModels] = useState<LocalModel[]>([])
  const [totalBytes, setTotalBytes] = useState(0)
  const [facets, setFacets] = useState<LibraryFacets | null>(null)
  const [library, setLibrary] = useState<LibraryQuery>(() => ({
    ...defaultLibraryQuery,
    query: searchParams.get('search') || '',
  }))
  const [preferences, setPreferences] = useState<UserPreferences>({})
  const density = densityOf(preferences)
  const families = familiesOf(preferences)
  const savedViews = savedViewsOf(preferences)
  const savedGroups = savedGroupsOf(preferences)
  const localPrefsRef = useRef(preferences)
  useEffect(() => {
    localPrefsRef.current = preferences
  }, [preferences])
  const visibleLocalColumns = columnsOf(preferences, 'local', LOCAL_COLUMN_IDS, LOCAL_COLUMNS_DEFAULT)
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const [scanning, setScanning] = useState(false)
  // Watched copies with upstream drift, by repo id. The rows' State
  // column shows this and nothing else — a blank state means "not
  // watched", which is the honest reading.
  const [drift, setDrift] = useState<Record<string, boolean>>({})
  useScrollMemory('local')
  const [error, setError] = useState('')

  const openModel = useCallback(
    (repoId: string) => navigate(modelHref(repoId)),
    [navigate],
  )

  const load = useCallback(() => {
    setError('')
    Promise.all([
      api.health(),
      api.localModels('', libraryQuerySearchParams(library)),
      api.watches().catch(() => null),
    ])
      .then(([healthPayload, modelPayload, watchPayload]) => {
        setHealth(healthPayload)
        setModels(modelPayload.items)
        setTotalBytes(modelPayload.total_bytes)
        setFacets(modelPayload.facets || null)
        if (watchPayload) {
          const next: Record<string, boolean> = {}
          for (const record of watchPayload.items as WatchRecord[]) {
            if (record.update_available) next[record.repo_id] = true
          }
          setDrift(next)
        }
      })
      .catch((reason) => setError(reason.message))
  }, [libraryQuerySignature(library)])

  useEffect(() => {
    const timer = window.setTimeout(load, 250)
    return () => window.clearTimeout(timer)
  }, [load])

  useEffect(() => {
    api
      .preferences()
      .then((payload) => setPreferences(payload.preferences))
      .catch(() => undefined)
  }, [])

  async function changeDensity(next: Density) {
    const current = localPrefsRef.current
    const optimistic = withDensity(current, next)
    localPrefsRef.current = optimistic
    setPreferences(optimistic)
    try {
      const stored = await api.savePreferences(optimistic)
      localPrefsRef.current = stored.preferences
      setPreferences(stored.preferences)
    } catch (reason) {
      localPrefsRef.current = current
      setPreferences(current)
      onToast(reason instanceof Error ? reason.message : 'Could not save the display preference.', 'error')
    }
  }

  async function changeFamilies(next: boolean) {
    await persistPreferences(
      withFamilies(localPrefsRef.current, next),
      'Could not save the display preference.',
    )
  }

  async function toggleLocalColumn(id: string) {
    const current = localPrefsRef.current
    const keep = new Set(columnsOf(current, 'local', LOCAL_COLUMN_IDS, LOCAL_COLUMNS_DEFAULT))
    if (keep.has(id)) keep.delete(id)
    else keep.add(id)
    const next = LOCAL_COLUMNS.map((column) => column.id).filter((columnId) => keep.has(columnId))
    const optimistic = withColumns(current, 'local', next)
    localPrefsRef.current = optimistic
    await persistPreferences(optimistic, 'Could not save the column preference.')
      .catch(() => {
        localPrefsRef.current = current
      })
  }

  function toggleFamily(name: string) {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  async function persistPreferences(next: UserPreferences, failure: string) {
    const previous = localPrefsRef.current
    localPrefsRef.current = next
    setPreferences(next)
    try {
      const stored = await api.savePreferences(next)
      localPrefsRef.current = stored.preferences
      setPreferences(stored.preferences)
    } catch (reason) {
      localPrefsRef.current = previous
      setPreferences(previous)
      onToast(reason instanceof Error ? reason.message : failure, 'error')
    }
  }

  async function saveView() {
    const name = window.prompt('Name this view', '')
    if (!name || !name.trim()) return
    await persistPreferences(
      addView(localPrefsRef.current, name.trim(), libraryQuerySignature(library)),
      'Could not save the view.',
    )
  }

  function applyView(view: SavedView) {
    setLibrary(parseLibraryQuery(view.query))
    setActiveGroup(null)
  }

  async function removeView(view: SavedView) {
    if (!window.confirm(`Delete the view "${view.name}"?`)) return
    await persistPreferences(withoutView(localPrefsRef.current, view.id), 'Could not delete the view.')
  }

  async function saveGroup() {
    if (models.length === 0) return
    const name = window.prompt(`Name a group of the ${models.length} model${models.length === 1 ? '' : 's'} currently shown`, '')
    if (!name || !name.trim()) return
    await persistPreferences(
      addGroup(localPrefsRef.current, name.trim(), models.map((model) => model.repo_id)),
      'Could not save the group.',
    )
  }

  function toggleGroup(group: SavedGroup) {
    setActiveGroup((current) => (current === group.name ? null : group.name))
  }

  async function removeGroup(group: SavedGroup) {
    if (!window.confirm(`Delete the group "${group.name}"? The model copies stay in the library.`)) return
    if (activeGroup === group.name) setActiveGroup(null)
    await persistPreferences(withoutGroup(localPrefsRef.current, group.name), 'Could not delete the group.')
  }

  const activeGroupMembers = activeGroup
    ? savedGroups.find((group) => group.name === activeGroup)?.repo_ids || []
    : []
  const shownModels = activeGroupMembers.length
    ? models.filter((model) => activeGroupMembers.includes(model.repo_id))
    : models

  async function scan() {
    setScanning(true)
    try {
      const result = await api.scanLocalModels()
      onToast(`Storage scan complete: ${result.count} model${result.count === 1 ? '' : 's'} indexed.`)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }

  const capacityPercent = health
    ? Math.min(100, ((health.storage.total_bytes - health.storage.free_bytes) / health.storage.total_bytes) * 100)
    : 0

  // Family grouping shared by the card grid and the rows surface.
  const groupsByName = useMemo(() => {
    const groups = new Map<string, LocalModel[]>()
    for (const model of shownModels) {
      const name = familyOf(model.repo_id)
      const bucket = groups.get(name)
      if (bucket) bucket.push(model)
      else groups.set(name, [model])
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [shownModels])

  return (
    <>
      <div className="standard-page">
        <div className="page-heading">
          <div>
            <h1>Library</h1>
          </div>
          <button className="secondary-button" onClick={scan} disabled={scanning}>
            {scanning ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}
            {scanning ? 'Scanning…' : 'Scan storage'}
          </button>
        </div>

        <section className="storage-strip">
          <div className="storage-icon">
            {health?.object_storage.enabled ? <Cloud size={23} /> : <HardDrive size={23} />}
          </div>
          <div className="storage-main">
            <div className="storage-title">
              <strong>
                {health?.object_storage.enabled
                  ? health.object_storage.connected ? 'S3 storage online' : 'S3 storage needs attention'
                  : health?.storage.writable ? 'Model storage online' : 'Model storage needs attention'}
              </strong>
              <code>
                {health?.object_storage.enabled
                  ? `s3://${health.object_storage.bucket}/${health.object_storage.prefix || ''}`
                  : health?.storage.path || '/models'}
              </code>
            </div>
            <div className="capacity-track" aria-label={`${capacityPercent.toFixed(0)} percent of volume used`}>
              <span style={{ width: `${capacityPercent}%` }} />
            </div>
            <div className="storage-meta">
              <span>{health ? `${formatBytes(health.storage.free_bytes)} free in local cache` : 'Reading volume…'}</span>
              <span>{formatBytes(totalBytes)} indexed models</span>
            </div>
          </div>
          <span className={
            health?.storage.writable && health?.object_storage.connected
              ? 'status-pill ok'
              : 'status-pill danger'
          }>
            {health?.storage.writable && health?.object_storage.connected
              ? <Check size={13} />
              : <AlertCircle size={13} />}
            {health?.object_storage.enabled
              ? health.object_storage.connected ? 'Connected' : 'Offline'
              : health?.storage.writable ? 'Writable' : 'Read only'}
          </span>
        </section>

        <div className="local-tools">
          <div className="catalog-search">
            <Search size={18} />
            <input
              value={library.query}
              onChange={(event) => setLibrary((current) => ({ ...current, query: event.target.value }))}
              placeholder="Search local models"
            />
          </div>
          <label className="sort-select">
            <select
              value={library.sort}
              onChange={(event) => setLibrary((current) => setSort(current, event.target.value as LibrarySort))}
              aria-label="Sort library"
            >
              {LIBRARY_SORTS.map((column) => (
                <option key={column} value={column}>
                  {columnLabels[column] || column}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="order-toggle"
              onClick={() => setLibrary((current) => ({
                ...current,
                order: current.order === 'asc' ? 'desc' : 'asc',
              }))}
              aria-label={`Order ${library.order === 'asc' ? 'ascending' : 'descending'}; click to flip`}
            >
              {library.order === 'asc' ? '↑' : '↓'}
            </button>
          </label>
          <div className="density-toggle" role="group" aria-label="Display density">
            <button
              type="button"
              className={density === 'cards' ? 'on' : ''}
              aria-pressed={density === 'cards'}
              onClick={() => changeDensity('cards')}
            >
              <LayoutGrid size={14} /> Cards
            </button>
            <button
              type="button"
              className={density === 'rows' ? 'on' : ''}
              aria-pressed={density === 'rows'}
              onClick={() => changeDensity('rows')}
            >
              <List size={14} /> Rows
            </button>
          </div>
          {density === 'rows' && (
            <ColumnsMenu
              spec={LOCAL_COLUMNS}
              visible={visibleLocalColumns}
              onToggle={toggleLocalColumn}
            />
          )}
          <button
            type="button"
            className={`secondary-button compact${families ? ' on' : ''}`}
            aria-pressed={families}
            onClick={() => changeFamilies(!families)}
            title="Group by publisher under expandable headings"
          >
            {families ? 'Families' : 'Flat'}
          </button>
          <span>{shownModels.length} of {facets ? facets.total : models.length} shown</span>
        </div>

        {(savedViews.length > 0 || savedGroups.length > 0 || models.length > 0) && (
          <div className="views-row" aria-label="Saved views and groups">
            <span className="views-label">Views</span>
            {savedViews.map((view) => (
              <span key={view.id} className={`view-chip${'' }`}>
                <button type="button" onClick={() => applyView(view)} title={view.query}>
                  {view.name}
                </button>
                <button
                  type="button"
                  className="chip-x"
                  onClick={() => removeView(view)}
                  aria-label={`Delete view ${view.name}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
            <button type="button" className="chip-add" onClick={saveView} title="Save the current filters, sort, and search as a view">
              + Save view
            </button>
            <span className="views-label">Groups</span>
            {savedGroups.map((group) => (
              <span key={group.name} className={`view-chip${activeGroup === group.name ? ' on' : ''}`}>
                <button
                  type="button"
                  onClick={() => toggleGroup(group)}
                  aria-pressed={activeGroup === group.name}
                  title={`${group.repo_ids.length} model${group.repo_ids.length === 1 ? '' : 's'} (manual member list)`}
                >
                  {group.name}
                </button>
                <button
                  type="button"
                  className="chip-x"
                  onClick={() => removeGroup(group)}
                  aria-label={`Delete group ${group.name}`}
                >
                  <X size={11} />
                </button>
              </span>
            ))}
            <button
              type="button"
              className="chip-add"
              onClick={saveGroup}
              disabled={models.length === 0}
              title="Save the currently shown models as a manual group"
            >
              + Save group
            </button>
          </div>
        )}

        {error && <div className="inline-error">{error}</div>}
        <div className="library-chassis">
          <LibraryRail
            facets={facets}
            active={library}
            onToggle={(key) => setLibrary((current) => toggleBooleanFacet(current, key))}
            onPick={(key, value) => setLibrary((current) => pickValueFacet(current, key, value))}
            onClear={() => setLibrary((current) => clearFacets(current))}
          />
          <div className="library-main">
            {density === 'rows' ? (
              <div
                className="rows-view"
                style={{ '--rows-grid': rowsGridTemplate(LOCAL_COLUMNS, visibleLocalColumns) } as CSSProperties}
              >
                <RowsHead
                  spec={LOCAL_COLUMNS}
                  visible={visibleLocalColumns}
                  sortColumn={library.sort === 'repo_id' ? 'name' : library.sort}
                  order={library.order}
                  onSort={(key) =>
                    setLibrary((current) =>
                      setSort(current, key === 'name' ? 'repo_id' : (key as LibrarySort)),
                    )
                  }
                />
                {families
                  ? groupsByName.map(([name, members]) => {
                      const isCollapsed = collapsed.has(name)
                      return (
                        <div key={name}>
                          <div
                            className={`row-fam${isCollapsed ? '' : ' open'}`}
                            role="button"
                            tabIndex={0}
                            aria-expanded={!isCollapsed}
                            onClick={() => toggleFamily(name)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault()
                                toggleFamily(name)
                              }
                            }}
                          >
                            <span className="caret"><ChevronRight size={13} /></span>
                            <span className="fam-name">{name}</span>
                            <span className="fam-meta">
                              {members.length} model{members.length === 1 ? '' : 's'} /{' '}
                              {formatBytes(members.reduce((sum, model) => sum + model.size_bytes, 0))}
                            </span>
                          </div>
                          {!isCollapsed &&
                            members.map((model) => (
                              <LocalModelRow
                                key={model.repo_id}
                                model={model}
                                onOpen={openModel}
                                visible={visibleLocalColumns}
                                updateAvailable={drift[model.repo_id]}
                                underFamily
                              />
                            ))}
                        </div>
                      )
                    })
                  : shownModels.map((model) => (
                      <LocalModelRow
                        key={model.repo_id}
                        model={model}
                        onOpen={openModel}
                        visible={visibleLocalColumns}
                        updateAvailable={drift[model.repo_id]}
                      />
                    ))}
              </div>
            ) : families ? (
              <div className="families-view">
                {groupsByName.map(([name, members]) => {
                  const isCollapsed = collapsed.has(name)
                  return (
                    <section key={name}>
                      <div className="family-head">
                        <button
                          type="button"
                          className={`family-caret${isCollapsed ? '' : ' open'}`}
                          onClick={() => toggleFamily(name)}
                          aria-expanded={!isCollapsed}
                          aria-label={`${isCollapsed ? 'Show' : 'Hide'} ${members.length} ${name} model${members.length === 1 ? '' : 's'}`}
                        >
                          <ChevronDown size={15} />
                        </button>
                        <h3>{name}</h3>
                        <span className="rule" />
                        <span className="gmeta">
                          {members.length} model{members.length === 1 ? '' : 's'},{' '}
                          {formatBytes(members.reduce((sum, model) => sum + model.size_bytes, 0))}
                        </span>
                      </div>
                      {!isCollapsed && (
                        <div className="matrix-grid">
                          {members.map((model) => (
                            <LocalMatrixCard key={model.repo_id} model={model} onOpen={openModel} updateAvailable={drift[model.repo_id]} />
                          ))}
                        </div>
                      )}
                    </section>
                  )
                })}
              </div>
            ) : (
              <div className="matrix-grid">
                {shownModels.map((model) => (
                  <LocalMatrixCard key={model.repo_id} model={model} onOpen={openModel} updateAvailable={drift[model.repo_id]} />
                ))}
              </div>
            )}
            {!error && shownModels.length === 0 && facets && facets.total > 0 && (
              <div className="empty-state spacious">
                <Filter size={34} />
                <h2>Nothing matches these filters</h2>
                <p>
                  {facets.total} indexed model{facets.total === 1 ? '' : 's'} sit outside the current view.
                </p>
                <button className="secondary-button" onClick={() => setLibrary((current) => clearFacets(current))}>
                  Clear filters
                </button>
              </div>
            )}
            {!error && models.length === 0 && facets === null && (
              <div className="empty-state spacious">
                <HardDrive size={34} />
                <h2>Your model library is empty</h2>
                <p>Download a model, copy a repository into the cache, or connect an S3 bucket.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

const activeDownloadStatuses = ['queued', 'preparing', 'downloading']

const downloadModeLabels = {
  full: 'Full repository',
  safetensors: 'SafeTensors',
  gguf: 'GGUF selection',
  metadata: 'Metadata only',
  custom: 'Custom selection',
}

type DownloadRowAction = 'pause' | 'resume' | 'restart' | 'move-to-front' | 'clear'

function DownloadStatus({
  job,
  onCancel,
  cancelling,
  onAction,
  busyKey,
  onOpen,
}: {
  job: DownloadJob
  onCancel?: (job: DownloadJob) => void
  cancelling?: boolean
  onAction?: (job: DownloadJob, action: DownloadRowAction) => void
  busyKey?: string | null
  onOpen?: (job: DownloadJob) => void
}) {
  const isActive = activeDownloadStatuses.includes(job.status)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const isPaused = job.status === 'paused'
  const isHistory = job.status === 'complete' || job.status === 'cancelled' || job.status === 'failed'
  const busy = (action: DownloadRowAction) => busyKey === `${job.id}:${action}`
  const remaining = Math.max(0, job.total_bytes - job.downloaded_bytes)
  const eta = job.speed_bps > 0 ? Math.round(remaining / job.speed_bps) : 0
  const etaLabel = eta
    ? eta > 3600
      ? `${Math.round(eta / 3600)}h remaining`
      : eta > 60
        ? `${Math.round(eta / 60)}m remaining`
        : `${eta}s remaining`
    : ''
  const modeLabel = downloadModeLabels[job.payload.mode || 'full']
  const outputTail = typeof job.metadata.output_tail === 'string' ? job.metadata.output_tail : ''
  const xetLog = typeof job.metadata.xet_log === 'string' ? job.metadata.xet_log : ''
  const engine = job.metadata.engine === 'xet' || job.metadata.engine === 'http' ? job.metadata.engine : ''

  async function copyError() {
    try {
      await navigator.clipboard.writeText(downloadErrorText(job))
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

  const open = onOpen
    ? () => onOpen(job)
    : undefined
  const rowAction = (action: DownloadRowAction) => (event: React.MouseEvent) => {
    event.stopPropagation()
    if (onAction) onAction(job, action)
  }
  return (
    <article className="download-row">
      <div className={`download-state-icon ${job.status}`}>
        {job.status === 'complete' ? (
          <PackageCheck size={19} />
        ) : job.status === 'failed' ? (
          <AlertCircle size={18} />
        ) : job.status === 'cancelled' ? (
          <Ban size={18} />
        ) : isPaused ? (
          <Pause size={18} />
        ) : (
          <ArrowDownToLine size={18} />
        )}
      </div>
      <div className="download-main">
        <div className="download-title">
          <div>
            <h3>{open ? <button type="button" className="download-model-link" onClick={open}>{job.repo_id}</button> : job.repo_id}</h3>
            <span>{modeLabel}, revision {job.revision}</span>
          </div>
          <div className="download-title-actions row-actions">
            <strong className={`job-status ${job.status}`}>{job.status}</strong>
            {job.status === 'queued' && onAction && (
              <button
                type="button"
                className="icon-btn"
                title="Move to front"
                aria-label="Move to front"
                disabled={busy('move-to-front')}
                onClick={rowAction('move-to-front')}
              >
                {busy('move-to-front') ? <LoaderCircle size={14} className="spin" /> : <ArrowUp size={14} />}
              </button>
            )}
            {isActive && onAction && (
              <button
                type="button"
                className="secondary-button compact"
                disabled={busy('pause')}
                onClick={rowAction('pause')}
              >
                {busy('pause') ? <LoaderCircle size={13} className="spin" /> : <Pause size={13} />}
                Pause
              </button>
            )}
            {isPaused && onAction && (
              <button
                type="button"
                className="secondary-button compact"
                disabled={busy('resume')}
                onClick={rowAction('resume')}
              >
                {busy('resume') ? <LoaderCircle size={13} className="spin" /> : <Play size={13} />}
                Resume
              </button>
            )}
            {isActive && onCancel && (
              <button
                type="button"
                className="cancel-download-button"
                onClick={(event) => {
                  event.stopPropagation()
                  onCancel(job)
                }}
                disabled={cancelling}
              >
                {cancelling ? <LoaderCircle size={14} className="spin" /> : <Ban size={14} />}
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
            {job.status === 'failed' && onAction && (
              <button
                type="button"
                className="secondary-button compact"
                disabled={busy('restart')}
                onClick={rowAction('restart')}
              >
                {busy('restart') ? <LoaderCircle size={13} className="spin" /> : <RotateCw size={13} />}
                Restart
              </button>
            )}
            {isHistory && onAction && (
              <button
                type="button"
                className="secondary-button compact danger"
                title="Files on disk are untouched."
                disabled={busy('clear')}
                onClick={rowAction('clear')}
              >
                {busy('clear') ? <LoaderCircle size={13} className="spin" /> : <X size={13} />}
                Clear
              </button>
            )}
          </div>
        </div>
        {isActive && (
          <>
            <div className="job-progress" aria-label={`${job.progress.toFixed(0)} percent downloaded`}>
              <span style={{ width: `${Math.max(job.progress, job.status === 'preparing' ? 2 : 0)}%` }} />
            </div>
            <div className="download-stats">
              <span>
                {formatBytes(job.downloaded_bytes)}
                {job.total_bytes > 0 && ` of ${formatBytes(job.total_bytes)}`}
              </span>
              <span>{job.speed_bps > 0 ? `${formatBytes(job.speed_bps)}/s` : 'Preparing repository…'}</span>
              {etaLabel && <span>{etaLabel}</span>}
              {typeof job.metadata.file_count === 'number' && <span>{job.metadata.file_count} repository files</span>}
            </div>
          </>
        )}
        {isPaused && (
          <div className="download-stats">
            <span>
              {formatBytes(job.downloaded_bytes)}
              {job.total_bytes > 0 && ` of ${formatBytes(job.total_bytes)}`} retained
            </span>
          </div>
        )}
        {job.status === 'complete' && (
          <div className="download-stats">
            <span>{formatBytes(job.downloaded_bytes)} stored</span>
            <span>Completed {relativeTime(job.completed_at)}</span>
            <code>{job.target_path}</code>
          </div>
        )}
        {job.status === 'cancelled' && (
          <div className="download-stats cancelled-copy">
            <span>{formatBytes(job.downloaded_bytes)} retained</span>
            <span>Partial files stay in place so a future download can resume.</span>
          </div>
        )}
        {job.status === 'failed' && (
          <details className="download-failure">
            <summary>{downloadErrorSummary(job)}</summary>
            <button
              type="button"
              className="secondary-button compact"
              onClick={copyError}
            >
              {copyState === 'copied' ? 'Copied' : 'Copy error'}
            </button>
            {copyState === 'failed' && <span role="status">Could not copy error.</span>}
            <div className="download-error">
              <AlertCircle size={15} />
              <div className="download-error-body">
                <pre className="download-error-message">{job.error}</pre>
                {outputTail && outputTail !== job.error && (
                  <pre className="worker-output-tail">{outputTail}</pre>
                )}
                <div className="download-diagnostics">
                  {engine && <span>{engine === 'xet' ? 'Xet transport' : 'HTTP transport'}</span>}
                  {xetLog && <code>{xetLog}</code>}
                </div>
              </div>
            </div>
          </details>
        )}
      </div>
    </article>
  )
}

function DownloadsPage({
  jobs,
  onToast,
  refreshDownloads,
  user,
}: {
  jobs: DownloadJob[]
  onToast: ToastHandler
  refreshDownloads: () => void
  user: User
}) {
  const [cancelling, setCancelling] = useState<string | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState<'pause-all' | 'resume-all' | 'clear' | null>(null)
  const [historyQuery, setHistoryQuery] = useState('')
  const [historyFilter, setHistoryFilter] = useState<'all' | 'failed'>('all')
  const navigate = useNavigate()
  useScrollMemory('downloads')
  const orderedJobs = useMemo(() => sortDownloads(jobs), [jobs])
  const running = orderedJobs.filter((job) => activeDownloadStatuses.includes(job.status))
  const paused = orderedJobs.filter((job) => job.status === 'paused')
  const active = [...running, ...paused]
  const finished = orderedJobs.filter((job) => !active.includes(job))
  const failedCount = jobs.filter((job) => job.status === 'failed').length
  const query = historyQuery.trim().toLowerCase()
  const visibleFinished = finished.filter(
    (job) =>
      (historyFilter === 'all' || job.status === 'failed') &&
      (query === '' || job.repo_id.toLowerCase().includes(query)),
  )
  const totalSpeed = running.reduce((total, job) => total + job.speed_bps, 0)
  const remainingBytes = running.reduce(
    (total, job) => total + Math.max(0, job.total_bytes - job.downloaded_bytes),
    0,
  )
  const completedCount = jobs.filter((job) => job.status === 'complete').length

  async function cancel(job: DownloadJob) {
    setCancelling(job.id)
    try {
      await api.cancelDownload(job.id)
      onToast(`${job.repo_id} was cancelled. Partial files were kept for resume.`)
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to cancel download', 'error')
    } finally {
      setCancelling(null)
    }
  }

  async function rowAction(job: DownloadJob, action: DownloadRowAction) {
    setBusyKey(`${job.id}:${action}`)
    const fallback: Record<DownloadRowAction, string> = {
      pause: 'Unable to pause download',
      resume: 'Unable to resume download',
      restart: 'Unable to restart download',
      'move-to-front': 'Unable to move download',
      clear: 'Unable to remove download',
    }
    try {
      if (action === 'pause') {
        await api.pauseDownload(job.id)
        onToast(`${job.repo_id} is paused.`)
      } else if (action === 'resume') {
        await api.resumeDownload(job.id)
        onToast(`${job.repo_id} is resuming.`)
      } else if (action === 'restart') {
        await api.restartDownload(job.id)
        onToast(`${job.repo_id} was restarted.`)
      } else if (action === 'move-to-front') {
        await api.moveDownloadToFront(job.id)
        onToast(`${job.repo_id} moved to the front of the queue.`)
      } else {
        await api.deleteDownload(job.id)
        onToast(`Removed ${job.repo_id} from history.`)
      }
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : fallback[action], 'error')
    } finally {
      setBusyKey(null)
    }
  }

  async function pauseAll() {
    setBulkBusy('pause-all')
    try {
      const result = await api.pauseAllDownloads()
      onToast(`${result.paused.length} downloads paused.`)
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to pause downloads', 'error')
    } finally {
      setBulkBusy(null)
    }
  }

  async function resumeAll() {
    setBulkBusy('resume-all')
    try {
      const result = await api.resumeAllDownloads()
      onToast(`${result.resumed.length} downloads resumed.`)
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to resume downloads', 'error')
    } finally {
      setBulkBusy(null)
    }
  }

  async function clearShown() {
    if (visibleFinished.length === 0) return
    setBulkBusy('clear')
    try {
      const result = await api.clearDownloads(visibleFinished.map((job) => job.id))
      if (result.deleted === 1 && visibleFinished.length === 1) {
        onToast(`Removed ${visibleFinished[0].repo_id} from history.`)
      } else if (result.deleted > 0) {
        onToast(
          `Removed ${result.deleted} ${result.deleted === 1 ? 'entry' : 'entries'} from history.`,
        )
      }
      refreshDownloads()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to clear history', 'error')
    } finally {
      setBulkBusy(null)
    }
  }

  return (
    <div className="standard-page downloads-page">
      <div className="page-heading">
        <div>
          <h1>Downloads</h1>
        </div>
      </div>

      <section className="transfer-overview" aria-label="Download activity summary">
        <div><span>Active transfers</span><strong>{running.length}</strong></div>
        <div><span>Combined speed</span><strong>{totalSpeed > 0 ? `${formatBytes(totalSpeed)}/s` : '—'}</strong></div>
        <div><span>Remaining</span><strong>{remainingBytes > 0 ? formatBytes(remainingBytes) : '—'}</strong></div>
        <div><span>Completed</span><strong>{completedCount}</strong></div>
        <div><span>Failed</span><strong className={failedCount > 0 ? 'danger' : undefined}>{failedCount}</strong></div>
      </section>

      <section className="download-section">
        <div className="history-heading">
          <h2>Active</h2>
          {active.length > 0 && (
            <div className="heading-tools">
              {running.length > 0 && (
                <button
                  type="button"
                  className="secondary-button compact"
                  disabled={bulkBusy !== null}
                  onClick={pauseAll}
                >
                  {bulkBusy === 'pause-all' ? <LoaderCircle size={13} className="spin" /> : <Pause size={13} />}
                  Pause all
                </button>
              )}
              {paused.length > 0 && (
                <button
                  type="button"
                  className="secondary-button compact"
                  disabled={bulkBusy !== null}
                  onClick={resumeAll}
                >
                  {bulkBusy === 'resume-all' ? <LoaderCircle size={13} className="spin" /> : <Play size={13} />}
                  Resume all
                </button>
              )}
            </div>
          )}
        </div>
        <div className="download-list">
          {active.map((job) => (
            <DownloadStatus
              key={job.id}
              job={job}
              onCancel={cancel}
              cancelling={cancelling === job.id}
              onAction={rowAction}
              busyKey={busyKey}
            />
          ))}
          {active.length === 0 && (
            <div className="empty-compact">
              <Clock3 size={18} /> No active downloads.
            </div>
          )}
        </div>
      </section>
      <section className="download-section">
        <div className="history-heading">
          <h2>History</h2>
          <div className="heading-tools">
            <div className="status-filter" role="group" aria-label="Filter history by status">
              <button
                type="button"
                className={historyFilter === 'all' ? 'on' : ''}
                onClick={() => setHistoryFilter('all')}
              >
                All<span className="n">{finished.length}</span>
              </button>
              <button
                type="button"
                className={historyFilter === 'failed' ? 'on' : ''}
                onClick={() => setHistoryFilter('failed')}
              >
                Failed<span className="n">{failedCount}</span>
              </button>
            </div>
            <div className="catalog-search compact-search">
              <Search size={16} />
              <input
                value={historyQuery}
                onChange={(event) => setHistoryQuery(event.target.value)}
                placeholder="Filter history"
              />
            </div>
            <button
              type="button"
              className="secondary-button compact danger"
              title="Files on disk are untouched."
              disabled={visibleFinished.length === 0 || bulkBusy !== null}
              onClick={clearShown}
            >
              {bulkBusy === 'clear' && <LoaderCircle size={13} className="spin" />}
              Clear {visibleFinished.length} shown
            </button>
          </div>
        </div>
        <div className="download-list">
          {visibleFinished.map((job) => (
            <DownloadStatus
              key={job.id}
              job={job}
              onOpen={() => navigate(modelHref(job.repo_id))}
              onAction={rowAction}
              busyKey={busyKey}
            />
          ))}
          {visibleFinished.length === 0 && (
            <div className="empty-compact">
              {historyFilter === 'failed' && failedCount === 0
                ? 'No failed downloads.'
                : query
                  ? 'No history matches this filter.'
                  : 'Completed, cancelled, and failed jobs appear here.'}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

const runtimeActiveStatuses = ['queued', 'preparing', 'transferring', 'loading']

function RuntimesPage() {
  const [targets, setTargets] = useState<RuntimeTarget[]>([])
  const [jobs, setJobs] = useState<RuntimeJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setError('')
    Promise.all([api.runtimeTargets(), api.runtimeJobs()])
      .then(([targetPayload, jobPayload]) => {
        setTargets(targetPayload.items)
        setJobs(jobPayload.items)
      })
      .catch((reason) => {
        const message = reason instanceof Error ? reason.message : 'Unable to read runtime targets.'
        setError(message)
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 2000)
    return () => window.clearInterval(timer)
  }, [load])

  return (
    <div className="standard-page runtimes-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Network inference destinations</span>
          <h1>Runtimes</h1>
          <p>Send cached models to destinations configured on this server.</p>
        </div>
        <button type="button" className="secondary-button" onClick={load} disabled={loading}>
          {loading ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}
          Refresh
        </button>
      </div>

      {error && <div className="inline-error">{error}</div>}

      <section className="runtime-target-grid">
        {targets.map((target) => (
          <article key={target.id} className="runtime-target-card">
            <div className={`runtime-kind-icon ${target.kind}`}>
              {target.kind === 'ollama' ? <Cloud size={20} /> : <Server size={20} />}
            </div>
            <div>
              <span>{target.kind === 'ollama' ? 'Ollama' : 'vLLM agent'}</span>
              <h2>{target.name}</h2>
              <code>{target.base_url}</code>
              <p>
                {target.transfer_mode === 'blob-upload'
                  ? `Model blobs transfer over the LAN, keep alive ${target.keep_alive || '5m'}`
                  : `Shared model root ${target.remote_model_root}`}
              </p>
            </div>
            <span className="status-pill ok"><Check size={13} /> Configured</span>
          </article>
        ))}
        {!loading && targets.length === 0 && (
          <div className="empty-state spacious runtime-empty">
            <Server size={34} />
            <h2>No destinations configured</h2>
            <p>This server has no send-to destinations.</p>
          </div>
        )}
      </section>

      <section className="runtime-history">
        <div className="section-heading-line">
          <div>
            <h2>Runtime jobs</h2>
          </div>
          <span>{jobs.filter((job) => runtimeActiveStatuses.includes(job.status)).length} active</span>
        </div>
        <div className="download-list">
          {jobs.map((job) => (
            <article key={job.id} className="runtime-job-row">
              <div className={`runtime-state-icon ${job.status}`}>
                {job.status === 'failed'
                  ? <AlertCircle size={18} />
                  : job.status === 'ready'
                    ? <Check size={18} />
                    : <Server size={18} />}
              </div>
              <div className="runtime-job-main">
                <div className="runtime-job-heading">
                  <div>
                    <h3>{job.runtime_model_name}</h3>
                    <span>{job.repo_id} → {job.target_name}</span>
                  </div>
                  <strong className={`job-status ${job.status}`}>{job.status}</strong>
                </div>
                <p>{job.error || job.message}</p>
                {runtimeActiveStatuses.includes(job.status) && (
                  <>
                    <div className="job-progress">
                      <span style={{ width: `${job.progress}%` }} />
                    </div>
                    <div className="download-stats">
                      <span>{job.progress.toFixed(0)}%</span>
                      {job.target_kind === 'ollama' && job.total_bytes > 0 && (
                        <span>{formatBytes(job.processed_bytes)} of {formatBytes(job.total_bytes)}</span>
                      )}
                      <span>Updated {relativeTime(job.updated_at)}</span>
                    </div>
                  </>
                )}
                {!runtimeActiveStatuses.includes(job.status) && (
                  <div className="download-stats">
                    <span>{job.target_kind}</span>
                    <span>Finished {relativeTime(job.completed_at || job.updated_at)}</span>
                    {job.source_file && <code>{job.source_file}</code>}
                  </div>
                )}
              </div>
            </article>
          ))}
          {!loading && jobs.length === 0 && (
            <div className="empty-compact">Load a model from its local-library page to create the first runtime job.</div>
          )}
        </div>
      </section>
    </div>
  )
}

const SETTINGS_SECTIONS = [
  { id: 'prefs', label: 'Preferences' },
  { id: 'views', label: 'Library views & groups' },
  { id: 'storage', label: 'Storage' },
  { id: 'hf', label: 'Hub access' },
  { id: 'accounts', label: 'Accounts' },
  { id: 'keys', label: 'Agent keys', adminOnly: true },
] as const

function SettingRow({
  k,
  v,
  act,
}: {
  k: ReactNode
  v: ReactNode
  act?: ReactNode
}) {
  return (
    <div className="sr">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
      <span className="act">{act}</span>
    </div>
  )
}

function SettingsPage({
  user,
  onToast,
}: {
  user: User
  onToast: ToastHandler
}) {
  const [health, setHealth] = useState<Health | null>(null)
  const [preferences, setPreferences] = useState<UserPreferences>({})
  const [active, setActive] = useState<string>('prefs')
  const [users, setUsers] = useState<User[]>([])
  const [keysCount, setKeysCount] = useState<number | null>(null)
  const density = densityOf(preferences)
  const families = familiesOf(preferences)
  const views = savedViewsOf(preferences)
  const groups = savedGroupsOf(preferences)

  const loadHealth = useCallback(() => {
    api.health().then(setHealth).catch(() => undefined)
  }, [])

  // One users list serves both the Accounts section and the mint form's
  // User select, so a freshly created member is mintable in the same
  // visit.
  const loadUsers = useCallback(() => {
    api.users().then((payload) => setUsers(payload.items)).catch(() => undefined)
  }, [])

  const settingsPrefsRef = useRef(preferences)
  useEffect(() => {
    settingsPrefsRef.current = preferences
  }, [preferences])

  useEffect(() => {
    loadHealth()
    loadUsers()
    api
      .preferences()
      .then((payload) => setPreferences(payload.preferences))
      .catch(() => undefined)
  }, [loadHealth, loadUsers])

  // Scrollspy: the nav follows the section whose top has passed the
  // reading line, without fighting the user's own clicks.
  useEffect(() => {
    let frame = 0
    const onScroll = () => {
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        let current: string = SETTINGS_SECTIONS[0].id
        for (const section of SETTINGS_SECTIONS) {
          const el = document.getElementById(section.id)
          if (el && el.getBoundingClientRect().top <= 120) current = section.id
        }
        setActive(current)
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.cancelAnimationFrame(frame)
    }
  }, [])

  function jumpTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setActive(id)
  }

  async function persist(next: UserPreferences, failure: string) {
    const previous = settingsPrefsRef.current
    settingsPrefsRef.current = next
    setPreferences(next)
    try {
      const stored = await api.savePreferences(next)
      settingsPrefsRef.current = stored.preferences
      setPreferences(stored.preferences)
    } catch (reason) {
      settingsPrefsRef.current = previous
      setPreferences(previous)
      onToast(reason instanceof Error ? reason.message : failure, 'error')
    }
  }

  const storageAttention = useMemo(() => {
    if (!health) return 0
    let n = 0
    if (!health.storage.writable) n += 1
    if (health.object_storage.enabled && !health.object_storage.connected) n += 1
    return n
  }, [health])

  const sectionCounts: Record<string, number | null> = {
    storage: storageAttention,
    accounts: users.length || null,
    keys: keysCount,
  }

  return (
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings sections">
        {SETTINGS_SECTIONS.filter((s) => !('adminOnly' in s && s.adminOnly) || user.role === 'admin').map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className={active === s.id ? 'active' : ''}
            aria-current={active === s.id ? 'true' : undefined}
            onClick={(event) => {
              event.preventDefault()
              jumpTo(s.id)
            }}
          >
            {s.label}
            {sectionCounts[s.id] ? <span className="n">{sectionCounts[s.id]}</span> : null}
          </a>
        ))}
      </nav>

      <section className="settings-content">
        <div className="page-heading">
          <h1>Settings</h1>
        </div>

        <div className="settings-section" id="prefs">
          <div className="s-head">
            <h2>Your preferences</h2>
          </div>
          <div className="settings-rows">
            <SettingRow
              k="Library display"
              v={
                <span className="density-toggle" role="group" aria-label="Display density">
                  <button
                    type="button"
                    className={density === 'cards' ? 'on' : ''}
                    aria-pressed={density === 'cards'}
                    onClick={() => persist(withDensity(settingsPrefsRef.current, 'cards'), 'Could not save the display preference.')}
                  >
                    Cards
                  </button>
                  <button
                    type="button"
                    className={density === 'rows' ? 'on' : ''}
                    aria-pressed={density === 'rows'}
                    onClick={() => persist(withDensity(settingsPrefsRef.current, 'rows'), 'Could not save the display preference.')}
                  >
                    Rows
                  </button>
                </span>
              }
            />
            <SettingRow
              k="Group by publisher"
              v={
                <span className="density-toggle" role="group" aria-label="Publisher families">
                  <button
                    type="button"
                    className={!families ? 'on' : ''}
                    aria-pressed={!families}
                    onClick={() => persist(withFamilies(settingsPrefsRef.current, false), 'Could not save the display preference.')}
                  >
                    Flat
                  </button>
                  <button
                    type="button"
                    className={families ? 'on' : ''}
                    aria-pressed={families}
                    onClick={() => persist(withFamilies(settingsPrefsRef.current, true), 'Could not save the display preference.')}
                  >
                    Families
                  </button>
                </span>
              }
            />
          </div>
        </div>

        <div className="settings-section" id="views">
          <div className="s-head">
            <h2>Library views &amp; groups</h2>
          </div>
          {views.length === 0 && groups.length === 0 ? (
            <p className="s-desc">None saved yet. Save one from the Library toolbar.</p>
          ) : (
            <div className="settings-rows">
              {views.map((view) => (
                <SettingRow
                  key={view.id}
                  k={<>View / <strong>{view.name}</strong></>}
                  v={<span className="mono">{view.query}</span>}
                  act={
                    <button
                      type="button"
                      className="secondary-button compact danger"
                      onClick={() => {
                        if (!window.confirm(`Delete the view "${view.name}"?`)) return
                        persist(withoutView(settingsPrefsRef.current, view.id), 'Could not delete the view.')
                      }}
                    >
                      <X size={13} /> Remove
                    </button>
                  }
                />
              ))}
              {groups.map((group) => (
                <SettingRow
                  key={group.name}
                  k={<>Group / <strong>{group.name}</strong></>}
                  v={
                    <span className="mono">
                      {group.repo_ids.length} model{group.repo_ids.length === 1 ? '' : 's'}
                    </span>
                  }
                  act={
                    <button
                      type="button"
                      className="secondary-button compact danger"
                      onClick={() => {
                        if (!window.confirm(`Delete the group "${group.name}"? The model copies stay in the library.`)) return
                        persist(withoutGroup(preferences, group.name), 'Could not delete the group.')
                      }}
                    >
                      <X size={13} /> Remove
                    </button>
                  }
                />
              ))}
            </div>
          )}
        </div>

        <div className="settings-section" id="storage">
          <div className="s-head">
            <h2>Storage</h2>
            {storageAttention > 0 && (
              <span className="state attn">
                <AlertCircle size={13} aria-hidden="true" />
                {storageAttention} item{storageAttention === 1 ? '' : 's'} need{storageAttention === 1 ? 's' : ''} attention
              </span>
            )}
          </div>
          <div className="settings-rows">
            <SettingRow
              k="Cache path"
              v={health ? <code>{health.storage.path}</code> : 'Reading…'}
            />
            <SettingRow
              k="Write access"
              v={
                health ? (
                  health.storage.writable ? (
                    <span className="state ok"><Check size={13} aria-hidden="true" />Writable</span>
                  ) : (
                    <span className="state bad">
                      <AlertCircle size={13} aria-hidden="true" />Not writable
                    </span>
                  )
                ) : (
                  'Reading…'
                )
              }
              act={
                <button type="button" className="secondary-button compact" onClick={loadHealth}>
                  <RefreshCw size={13} /> Re-check
                </button>
              }
            />
            <SettingRow
              k="Free space"
              v={
                health
                  ? `${formatBytes(health.storage.free_bytes)} of ${formatBytes(health.storage.total_bytes)}`
                  : 'Reading…'
              }
            />
            <SettingRow
              k="Metadata database"
              v={health ? (health.database_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite') : 'Reading…'}
            />
            {health?.library_db?.legacy_file && (
              <SettingRow
                k="Library backup present"
                v={
                  <>
                    This library was migrated from <code>{health.library_db.legacy_file}</code>{' '}
                    (last written{' '}
                    {health.library_db.legacy_mtime
                      ? new Date(health.library_db.legacy_mtime * 1000).toLocaleDateString(undefined, {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                        })
                      : 'unknown'}
                    , {health.library_db.legacy_bytes != null ? formatBytes(health.library_db.legacy_bytes) : 'unknown size'}
                    ). The original file is kept intact as a backup, feel free to delete it whenever you're ready.
                  </>
                }
              />
            )}
            {health?.object_storage.enabled && (
              <>
                <SettingRow
                  k="S3 location"
                  v={
                    <code>
                      s3://{health.object_storage.bucket}/{health.object_storage.prefix || ''}
                    </code>
                  }
                  act={
                    health.object_storage.connected ? (
                      <span className="state ok"><Check size={13} aria-hidden="true" />Connected</span>
                    ) : (
                      <span className="state bad">
                        <AlertCircle size={13} aria-hidden="true" />
                        {health.object_storage.error || 'Unavailable'}
                      </span>
                    )
                  }
                />
              </>
            )}
          </div>
        </div>

        <div className="settings-section" id="hf">
          <div className="s-head">
            <h2>Hub access</h2>
          </div>
          <div className="settings-rows">
            <SettingRow
              k="Endpoint"
              v={health ? <code>{health.hf_endpoint}</code> : 'Reading…'}
            />
            <SettingRow
              k="Token"
              v={
                health ? (
                  health.hf_token_configured ? (
                    <span className="state ok"><Check size={13} aria-hidden="true" />Configured</span>
                  ) : (
                    <span className="state bad">
                      <AlertCircle size={13} aria-hidden="true" />Not configured
                    </span>
                  )
                ) : (
                  'Reading…'
                )
              }
            />
          </div>
        </div>

        <AccountAdmin user={user} users={users} onUsersChanged={loadUsers} onToast={onToast} />
        {user.role === 'admin' && (
          <AgentKeysAdmin user={user} users={users} onToast={onToast} onCount={setKeysCount} />
        )}

        <div className="runtime-line">
          <span>
            {BRAND.displayName} {health?.version || '1.0.0'}
          </span>
          <span className="sep">/</span>
          <span>
            Copyright 2026{' '}
            <a href="https://toks.love" target="_blank" rel="noreferrer" className="quiet-link">
              toks.love
            </a>
          </span>
        </div>
      </section>
    </div>
  )
}

function Application({
  authStatus,
  onAuthChange,
}: {
  authStatus: AuthStatus
  onAuthChange: (status: AuthStatus) => void
}) {
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null)

  const showToast = useCallback((message: string, tone: ToastTone = 'success') => {
    setToast({ message, tone })
  }, [])

  const refreshDownloads = useCallback(() => {
    api
      .downloads()
      .then((payload) => setJobs(payload.items))
      .catch(() => undefined)
  }, [])

  const [watchList, setWatchList] = useState<WatchList | null>(null)
  const refreshWatches = useCallback(() => {
    api
      .watches()
      .then(setWatchList)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    refreshWatches()
  }, [refreshWatches])

  // The poller's cadence is minutes; this slow badge poll only keeps
  // the nav count honest across other people's changes. Window focus
  // refreshes immediately.
  useEffect(() => {
    const timer = window.setInterval(refreshWatches, 45000)
    const onFocus = () => refreshWatches()
    window.addEventListener('focus', onFocus)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [refreshWatches])

  useEffect(() => {
    refreshDownloads()
    const timer = window.setInterval(refreshDownloads, 1800)
    return () => window.clearInterval(timer)
  }, [refreshDownloads])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 4500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const activeDownloads = useMemo(
    () => jobs.filter((job) => ['queued', 'preparing', 'downloading'].includes(job.status)).length,
    [jobs],
  )

  const user = authStatus.user as User

  async function logout() {
    try {
      await api.logout()
    } finally {
      onAuthChange({ ...authStatus, user: null, csrf_token: null })
    }
  }

  return (
    <Shell
      activeDownloads={activeDownloads}
      staleWatches={watchList?.stale_count ?? 0}
      user={user}
      onLogout={logout}
    >
      <Routes>
        <Route path="/" element={<Navigate to="/models" replace />} />
        <Route
          path="/models"
        element={
          <ModelsPage onToast={showToast} refreshDownloads={refreshDownloads} user={user} />
        }
        />
        <Route
          path="/model/:owner/:name"
          element={
            <ModelPage
              onQueued={refreshDownloads}
              onChanged={refreshDownloads}
              onToast={showToast}
              canManageRuntimes={user.role === 'admin'}
              user={user}
              jobs={jobs}
            />
          }
        />
        <Route path="/local" element={<LocalPage onToast={showToast} user={user} />} />
        <Route path="/saved" element={<SavedPage onToast={showToast} user={user} />} />
        <Route path="/watched" element={<WatchedPage onToast={showToast} />} />
        <Route path="/uploads" element={<UploadsPage user={user} onToast={showToast} />} />
        <Route
          path="/downloads"
          element={
            <DownloadsPage
              jobs={jobs}
              onToast={showToast}
              refreshDownloads={refreshDownloads}
              user={user}
            />
          }
        />
        <Route
          path="/runtimes"
          element={
            user.role === 'admin'
              ? <RuntimesPage />
              : <Navigate to="/local" replace />
          }
        />
        <Route path="/settings" element={<SettingsPage user={user} onToast={showToast} />} />
        <Route path="*" element={<Navigate to="/models" replace />} />
      </Routes>
      {toast && (
        <div className={`toast ${toast.tone}`} role="status">
          {toast.tone === 'error' ? <AlertCircle size={16} /> : <Check size={16} />}
          {toast.message}
        </div>
      )}
    </Shell>
  )
}

export default function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [error, setError] = useState('')

  const refreshAuth = useCallback(() => {
    setError('')
    api
      .authStatus()
      .then(setStatus)
      .catch((reason) => setError(reason instanceof Error ? reason.message : `Unable to reach ${BRAND.displayName}`))
  }, [])

  useEffect(() => {
    refreshAuth()
    window.addEventListener(BRAND.unauthorizedEvent, refreshAuth)
    return () => window.removeEventListener(BRAND.unauthorizedEvent, refreshAuth)
  }, [refreshAuth])

  if (error) {
    return (
      <main className="auth-layout auth-unavailable">
        <section className="auth-card">
          <AlertCircle size={28} />
          <h1>{BRAND.displayName} is unavailable</h1>
          <p>{error}</p>
          <button className="secondary-button" onClick={refreshAuth}><RefreshCw size={16} /> Retry</button>
        </section>
      </main>
    )
  }

  if (!status) {
    return (
      <main className="app-loading">
        <BrandMark className="app-loading-mark" />
        <LoaderCircle size={23} className="spin" />
        <span>Opening your model library…</span>
      </main>
    )
  }

  if (status.setup_required || !status.user) {
    return <AuthScreen setup={status.setup_required} onAuthenticated={setStatus} />
  }

  return (
    <HashRouter>
      <Application authStatus={status} onAuthChange={setStatus} />
    </HashRouter>
  )
}
