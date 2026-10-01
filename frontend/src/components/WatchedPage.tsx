import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  Download,
  Eye,
  LoaderCircle,
  Pin,
  RefreshCw,
} from 'lucide-react'
import { api } from '../api'
import type { WatchRecord } from '../types'
import {
  UPDATE_STATUS_LABELS,
  applyableUpdateRows,
  reportOnlyUpdateRows,
  transferBytesOf,
  updateSummaryLine,
  type UpdateDiff,
} from '../updateDiff.ts'
import { columnsOf, withColumns, type UserPreferences } from '../libraryQuery'
import {
  formatBytes,
  formatParameterCount,
  modelHref,
  relativeTime,
  taskLabel,
} from '../utils'
import {
  ColumnsMenu,
  RowsHead,
  WATCHED_COLUMNS,
  WATCHED_COLUMNS_DEFAULT,
  WATCHED_COLUMN_IDS,
  rowsGridTemplate,
} from './RepositoryRows'
import { useNavigate } from 'react-router-dom'
import { useScrollMemory } from '../useScrollMemory'

type ToastHandler = (message: string, tone?: 'success' | 'error') => void
type GroupId = 'attention' | 'current' | 'unchecked'

const GROUPS: { id: GroupId; title: string }[] = [
  { id: 'attention', title: 'Needs attention' },
  { id: 'current', title: 'Up to date' },
  { id: 'unchecked', title: 'Not checked yet' },
]

function groupOf(row: WatchRecord): GroupId {
  if (row.update_available) return 'attention'
  if (row.checked_at) return 'current'
  return 'unchecked'
}

function copyLocation(copy: NonNullable<WatchRecord['copy']>): string {
  if (copy.storage_backend === 's3') return copy.cached ? 'S3 cached' : 'S3 only'
  if (copy.location && copy.location !== 'primary') return copy.location
  return 'local'
}

export function WatchedPage({ onToast }: { onToast: ToastHandler }) {
  const [items, setItems] = useState<WatchRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)
  const [preferences, setPreferences] = useState<UserPreferences | null>(null)
  const [stateFirst, setStateFirst] = useState<GroupId>('attention')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [diff, setDiff] = useState<UpdateDiff | null>(null)
  const [reviewPhase, setReviewPhase] = useState<'loading' | 'ready'>('loading')
  const [reviewError, setReviewError] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [applying, setApplying] = useState(false)
  const navigate = useNavigate()
  useScrollMemory('watched')

  const load = useCallback(() => {
    setError('')
    api
      .watches()
      .then((payload) => setItems(payload.items))
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load the watch list.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  const preferencesRef = useRef<UserPreferences | null>(null)
  useEffect(() => {
    preferencesRef.current = preferences
  }, [preferences])

  useEffect(() => {
    api
      .preferences()
      .then((payload) => setPreferences(payload.preferences))
      .catch(() => setPreferences(null))
  }, [])

  const visible = useMemo(
    () => columnsOf(preferences, 'watched', WATCHED_COLUMN_IDS, WATCHED_COLUMNS_DEFAULT),
    [preferences],
  )

  function toggleColumn(id: string) {
    const current = preferencesRef.current
    const stored = columnsOf(current, 'watched', WATCHED_COLUMN_IDS, WATCHED_COLUMNS_DEFAULT)
    const next = stored.includes(id) ? stored.filter((column) => column !== id) : [...stored, id]
    const updated = withColumns(current, 'watched', next)
    preferencesRef.current = updated
    setPreferences(updated)
    api.savePreferences(updated).catch(() => undefined)
  }

  function checkNow() {
    setChecking(true)
    api
      .checkWatchNow()
      .then(() => load())
      .catch((reason) => onToast(reason instanceof Error ? reason.message : 'Could not check the hub.', 'error'))
      .finally(() => setChecking(false))
  }

  function openReview(row: WatchRecord) {
    if (expanded === row.repo_id) {
      setExpanded(null)
      setDiff(null)
      setReviewError('')
      return
    }
    setExpanded(row.repo_id)
    setDiff(null)
    setReviewError('')
    setPicked(new Set())
    setReviewPhase('loading')
    api
      .localModelUpdates(row.repo_id)
      .then((payload) => {
        setDiff(payload)
        // Every applyable file is picked by default.
        setPicked(new Set(applyableUpdateRows(payload).map((applyable) => applyable.path)))
      })
      .catch((reason) =>
        setReviewError(reason instanceof Error ? reason.message : 'Could not compare against upstream.'),
      )
      .finally(() => setReviewPhase('ready'))
  }

  function togglePicked(path: string, checked: boolean) {
    setPicked((current) => {
      const next = new Set(current)
      if (checked) next.add(path)
      else next.delete(path)
      return next
    })
  }

  async function unwatch(row: WatchRecord) {
    try {
      await api.unwatchModel(row.repo_id)
      onToast(`${row.repo_id} is no longer watched.`)
      if (expanded === row.repo_id) setExpanded(null)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to unwatch.', 'error')
    }
  }

  async function markSeen(row: WatchRecord) {
    try {
      await api.markWatchSeen(row.repo_id)
      onToast(`${row.repo_id} acknowledged.`)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Nothing to acknowledge yet.', 'error')
    }
  }

  async function applyUpdates(row: WatchRecord) {
    if (!diff) return
    const paths = applyableUpdateRows(diff).filter((applyable) => picked.has(applyable.path)).map((applyable) => applyable.path)
    if (paths.length === 0) return
    setApplying(true)
    try {
      await api.applyLocalModelUpdates(row.repo_id, paths)
      onToast(`Update queued for ${row.repo_id}.`)
      setExpanded(null)
      setDiff(null)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Could not queue the update.', 'error')
    } finally {
      setApplying(false)
    }
  }

  const lastChecked = items.reduce<string | null>((latest, item) => {
    if (!item.checked_at) return latest
    if (!latest || item.checked_at > latest) return item.checked_at
    return latest
  }, null)

  const grouped = useMemo(() => {
    const buckets: Record<GroupId, WatchRecord[]> = { attention: [], current: [], unchecked: [] }
    for (const item of items) buckets[groupOf(item)].push(item)
    buckets.attention.sort((a, b) => (b.change_detected_at || '').localeCompare(a.change_detected_at || ''))
    buckets.current.sort((a, b) => (b.checked_at || '').localeCompare(a.checked_at || ''))
    buckets.unchecked.sort((a, b) => a.repo_id.localeCompare(b.repo_id))
    const order: GroupId[] =
      stateFirst === 'attention'
        ? ['attention', 'current', 'unchecked']
        : ['current', 'attention', 'unchecked']
    return order.map((id) => ({ id, rows: buckets[id] }))
  }, [items, stateFirst])

  const staleCount = items.filter((item) => item.update_available).length

  function stateCell(row: WatchRecord) {
    if (row.update_available) {
      const when = row.change_detected_at ? relativeTime(row.change_detected_at) : ''
      return (
        <span className="state attn">
          <span className="glyph" aria-hidden="true">↻</span>
          {row.has_copy ? 'Update available' : 'Changed upstream'}
          {when && <span className="detail">changed {when}</span>}
        </span>
      )
    }
    if (row.checked_at) {
      return (
        <span className="state ok">
          <span className="glyph" aria-hidden="true">✓</span>
          Up to date
        </span>
      )
    }
    return <span className="state muted">Not checked yet</span>
  }

  function renderRow(row: WatchRecord, group: GroupId) {
    const [owner, ...rest] = row.repo_id.split('/')
    const name = rest.join('/')
    const copy = row.copy
    const meta = row.metadata || {}
    const isStale = group === 'attention'
    const cells: Record<string, ReactNode> = {
      kind: meta.pipeline_tag ? (
        <span className="c-kind">{taskLabel(meta.pipeline_tag)}</span>
      ) : (
        <span className="c-empty">—</span>
      ),
      size: copy?.size_bytes ? (
        <span className="c-size">{formatBytes(copy.size_bytes)}</span>
      ) : (
        <span className="c-empty">—</span>
      ),
      params: copy?.parameter_count ? (
        <span className="c-params">{formatParameterCount(copy.parameter_count)}</span>
      ) : (
        <span className="c-empty">—</span>
      ),
      location: copy ? <span className="c-loc">{copyLocation(copy)}</span> : <span className="c-empty">—</span>,
      state: stateCell(row),
      note: row.note_preview ? (
        <span className="c-note" title={row.note_preview.body}>
          {row.note_preview.pinned && <Pin size={10} className="pin-glyph" aria-hidden="true" />}
          {row.note_preview.body}
        </span>
      ) : (
        <span className="c-empty">—</span>
      ),
      checked: row.checked_at ? (
        <span className="c-mod">{relativeTime(row.checked_at)}</span>
      ) : (
        <span className="c-empty">—</span>
      ),
      actions: (
        <span className="row-actions" onClick={(event) => event.stopPropagation()}>
          {isStale && row.has_copy && (
            <button
              type="button"
              className="download-button compact"
              onClick={() => openReview(row)}
              aria-expanded={expanded === row.repo_id}
            >
              Review diff
            </button>
          )}
          {isStale && !row.has_copy && (
            <button type="button" className="secondary-button compact" onClick={() => markSeen(row)}>
              Mark seen
            </button>
          )}
          <button type="button" className="secondary-button compact" onClick={() => unwatch(row)}>
            Unwatch
          </button>
        </span>
      ),
    }

    return (
      <div key={row.repo_id}>
        <div
          className="row clickable-row"
          role="button"
          tabIndex={0}
          onClick={() => navigate(modelHref(row.repo_id))}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              navigate(modelHref(row.repo_id))
            }
          }}
        >
          <span className="c-name">
            {owner !== row.repo_id && <span className="org">{owner}/</span>}
            {name}
          </span>
          {WATCHED_COLUMNS.filter((column) => visible.includes(column.id)).map((column) => (
            <span key={column.id} className={`c-${column.id}${column.align ? ` c-${column.align}` : ''}`}>
              {cells[column.id]}
            </span>
          ))}
          {cells.actions}
        </div>
        {expanded === row.repo_id && (
          <div className="diff-panel-row" onClick={(event) => event.stopPropagation()}>
            {reviewPhase === 'loading' && (
              <div className="diff-loading">
                <LoaderCircle size={15} className="spin" /> Comparing against upstream…
              </div>
            )}
            {reviewError && <div className="inline-error">{reviewError}</div>}
            {diff && reviewPhase === 'ready' && (
              <>
                <div className="diff-head">
                  <div>
                    <h2>Update diff</h2>
                    <p className="meta-line">
                      {diff.local.sha ? diff.local.sha.slice(0, 7) : 'no revision'}
                      <span className="sep">→</span>
                      {diff.upstream.sha.slice(0, 7)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="download-button"
                    disabled={applying || picked.size === 0}
                    onClick={() => applyUpdates(row)}
                  >
                    {applying ? <LoaderCircle size={15} className="spin" /> : <Download size={15} />}
                    Apply selected
                    <small>{formatBytes(transferBytesOf(applyableUpdateRows(diff).filter((applyable) => picked.has(applyable.path))))}</small>
                  </button>
                </div>
                <p className="updates-note">{updateSummaryLine(diff)}, about {formatBytes(transferBytesOf(applyableUpdateRows(diff)))} to transfer</p>
                <div className="file-table diff-table">
                  <div className="ft-head diff-head-row">
                    <span />
                    <span className="diff-glyph-head" />
                    <span>File</span>
                    <span className="ft-size-head">On disk</span>
                    <span className="ft-size-head">Upstream</span>
                  </div>
                  <div className="ft-scroll">
                    {applyableUpdateRows(diff).map((applyable) => (
                      <label key={applyable.path} className="ft-row diff-row" title={UPDATE_STATUS_LABELS[applyable.status]}>
                        <input
                          type="checkbox"
                          checked={picked.has(applyable.path)}
                          onChange={(event) => togglePicked(applyable.path, event.target.checked)}
                        />
                        <span className={`diff-glyph ${applyable.status}`} aria-hidden="true">
                          {applyable.status === 'added' ? '+' : applyable.status === 'changed' ? '~' : '↻'}
                        </span>
                        <span className="ft-path" title={applyable.path}>{applyable.path}</span>
                        <span className="ft-size">{applyable.status === 'added' ? '—' : formatBytes(applyable.local.size)}</span>
                        <span className="ft-size">{formatBytes(applyable.upstream?.size || 0)}</span>
                      </label>
                    ))}
                    {reportOnlyUpdateRows(diff).map((removed) => (
                      <div key={removed.path} className="ft-row diff-row removed" title={UPDATE_STATUS_LABELS.removed}>
                        <span />
                        <span className="diff-glyph removed" aria-hidden="true">−</span>
                        <span className="ft-path" title={removed.path}>{removed.path}</span>
                        <span className="ft-size">{formatBytes(removed.local.size)}</span>
                        <span className="ft-size">—</span>
                      </div>
                    ))}
                  </div>
                </div>
                {diff.updates_available && reportOnlyUpdateRows(diff).length > 0 && (
                  <p className="updates-note">Removed upstream — kept locally until you delete it.</p>
                )}
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="standard-page">
      <div className="page-heading">
        <div>
          <h1>Watched</h1>
          <p className="meta-line">
            {loading ? 'loading…' : `${items.length} repository${items.length === 1 ? '' : 'ies'}`}
            <span className="sep">/</span>
            {lastChecked ? `checked ${relativeTime(lastChecked)}` : 'no checks yet'}
          </p>
        </div>
        <div className="heading-actions">
          <ColumnsMenu spec={WATCHED_COLUMNS} visible={visible} onToggle={toggleColumn} />
          <button className="secondary-button" onClick={checkNow} disabled={checking || loading || items.length === 0}>
            {checking ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}
            Check now
          </button>
        </div>
      </div>

      {error && <div className="inline-error">{error}</div>}

      {!loading && items.length === 0 && (
        <div className="empty-state spacious">
          <Eye size={34} />
          <h2>Nothing is being watched yet</h2>
          <p>Watch a model from its page to be told when upstream changes.</p>
        </div>
      )}

      {!loading &&
        grouped.map((group) =>
          group.rows.length === 0 ? null : (
            <section key={group.id} className={`watch-group${group.id === 'attention' ? ' attn' : ''}`}>
              <div className="watch-group-head">
                <h2>{GROUPS.find((definition) => definition.id === group.id)?.title}</h2>
                <span className="rule" aria-hidden="true" />
                <span className="n">{group.rows.length}</span>
              </div>
              <div className="rows-view" style={{ '--rows-grid': rowsGridTemplate(WATCHED_COLUMNS, visible) + ' auto' } as CSSProperties}>
                <RowsHead
                  spec={WATCHED_COLUMNS}
                  visible={visible}
                  sortColumn="state"
                  order={stateFirst === 'attention' ? 'desc' : 'asc'}
                  onSort={() => setStateFirst((current) => (current === 'attention' ? 'current' : 'attention'))}
                />
                {group.rows.map((row) => renderRow(row, group.id))}
              </div>
            </section>
          ),
        )}

      {!loading && items.length > 0 && (
        <p className="watch-foot">
          {staleCount > 0
            ? `${staleCount} of ${items.length} watched repository${items.length === 1 ? '' : 'ies'} changed upstream`
            : `No upstream changes on ${items.length} watched repository${items.length === 1 ? '' : 'ies'}`}
          {lastChecked ? (
            <>
              <span className="sep">/</span> last check {relativeTime(lastChecked)}
            </>
          ) : null}
        </p>
      )}
    </div>
  )
}
