import { useState, type KeyboardEvent, type ReactNode } from 'react'
import {
  Check,
  ChevronDown,
  Columns3,
  Download,
  Heart,
  LoaderCircle,
  LockKeyhole,
  Pin,
  RefreshCw,
} from 'lucide-react'
import type { HubModel, LocalModel } from '../types'
import { familyOf } from '../libraryQuery'
import { formatBytes, formatNumber, formatParameterCount, relativeTime, taskLabel } from '../utils'

// ---------- Column customization ----------

// The rows surface is a table with a user-selectable column set,
// persisted per account per surface. "name" is always visible; the
// registry order is the display order. A column without a sort key is
// a plain label in the header.
export interface ColumnSpec {
  id: string
  label: string
  track: string
  align?: 'right'
  sortKey?: string
}

export const NAME_TRACK = 'minmax(0, 1.6fr)'

export const LOCAL_COLUMNS: ColumnSpec[] = [
  { id: 'kind', label: 'Kind', track: '105px', sortKey: 'pipeline_tag' },
  { id: 'size', label: 'Size', track: '84px', align: 'right', sortKey: 'size_bytes' },
  { id: 'params', label: 'Params', track: '84px', align: 'right', sortKey: 'parameter_count' },
  { id: 'library', label: 'Library', track: '110px', sortKey: 'library_name' },
  { id: 'location', label: 'Location', track: '110px', sortKey: 'location' },
  { id: 'state', label: 'State', track: '130px', sortKey: 'state' },
  { id: 'note', label: 'Note', track: 'minmax(0, 1.1fr)' },
  { id: 'modified', label: 'Modified', track: '92px', align: 'right', sortKey: 'modified_at' },
  { id: 'files', label: 'File count', track: '78px', align: 'right', sortKey: 'file_count' },
  { id: 'downloaded', label: 'Downloaded', track: '92px', align: 'right', sortKey: 'downloaded_at' },
]
// Defaults: the card's facts (size, params, library) on, note, file
// count, and downloaded off.
export const LOCAL_COLUMNS_DEFAULT: string[] = ['kind', 'size', 'params', 'library', 'location', 'state', 'modified']
export const LOCAL_COLUMN_IDS: string[] = LOCAL_COLUMNS.map((column) => column.id)

export const HUB_COLUMNS: ColumnSpec[] = [
  { id: 'kind', label: 'Kind', track: '110px', sortKey: 'kind' },
  { id: 'params', label: 'Params', track: '84px', align: 'right', sortKey: 'params' },
  { id: 'quants', label: 'Quants', track: '78px', align: 'right', sortKey: 'quants' },
  { id: 'library', label: 'Library', track: '110px', sortKey: 'library' },
  { id: 'state', label: 'State', track: '120px', sortKey: 'state' },
  { id: 'downloads', label: 'Downloads', track: '92px', align: 'right', sortKey: 'downloads' },
  { id: 'likes', label: 'Likes', track: '78px', align: 'right', sortKey: 'likes' },
  { id: 'modified', label: 'Modified', track: '92px', align: 'right', sortKey: 'modified' },
]
export const HUB_COLUMNS_DEFAULT: string[] = ['kind', 'params', 'quants', 'library', 'state', 'downloads', 'likes', 'modified']
export const HUB_COLUMN_IDS: string[] = HUB_COLUMNS.map((column) => column.id)

export const WATCHED_COLUMNS: ColumnSpec[] = [
  { id: 'kind', label: 'Kind', track: '110px', sortKey: 'kind' },
  { id: 'size', label: 'Size', track: '84px', align: 'right', sortKey: 'size' },
  { id: 'params', label: 'Params', track: '84px', align: 'right', sortKey: 'params' },
  { id: 'location', label: 'Location', track: '110px', sortKey: 'location' },
  { id: 'state', label: 'State', track: '260px', sortKey: 'state' },
  { id: 'note', label: 'Note', track: 'minmax(0, 1.1fr)', sortKey: 'note' },
  { id: 'checked', label: 'Checked', track: '92px', align: 'right', sortKey: 'checked' },
]
// This page is about drift, not acquisition: params and note start off.
export const WATCHED_COLUMNS_DEFAULT: string[] = ['kind', 'size', 'location', 'state', 'checked']
export const WATCHED_COLUMN_IDS: string[] = WATCHED_COLUMNS.map((column) => column.id)

export function visibleColumns(spec: ColumnSpec[], visible: string[]): ColumnSpec[] {
  return spec.filter((column) => visible.includes(column.id))
}

export function rowsGridTemplate(spec: ColumnSpec[], visible: string[]): string {
  const tracks = [NAME_TRACK]
  for (const column of spec) if (visible.includes(column.id)) tracks.push(column.track)
  return tracks.join(' ')
}

export function ColumnsMenu({
  spec,
  visible,
  onToggle,
}: {
  spec: ColumnSpec[]
  visible: string[]
  onToggle: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <span className="columns-menu">
      <button
        type="button"
        className="secondary-button compact"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Columns3 size={13} />
        Columns
        <ChevronDown size={11} />
      </button>
      {open && (
        <>
          <span className="cm-backdrop" onClick={() => setOpen(false)} aria-hidden="true" />
          <span className="cm-panel" role="menu" aria-label="Visible columns">
            <span className="cm-title">Visible columns</span>
            <label className="locked">
              <input type="checkbox" checked disabled /> Name <span className="lock">always</span>
            </label>
            {spec.map((column) => (
              <label key={column.id}>
                <input
                  type="checkbox"
                  checked={visible.includes(column.id)}
                  onChange={() => onToggle(column.id)}
                />
                {column.label}
              </label>
            ))}
          </span>
        </>
      )}
    </span>
  )
}

// ---------- Shared row head ----------

export function RowsHead({
  spec,
  visible,
  sortColumn,
  order,
  onSort,
}: {
  spec: ColumnSpec[]
  visible: string[]
  sortColumn?: string | null
  order?: 'asc' | 'desc'
  onSort?: (sortKey: string) => void
}) {
  const nameActive = onSort && sortColumn === 'name'
  return (
    <div className="rows-head">
      {onSort ? (
        <button
          type="button"
          className={`h${nameActive ? ' active' : ''}`}
          onClick={() => onSort('name')}
          aria-label="Sort by name"
        >
          Name {nameActive ? (order === 'asc' ? '↑' : '↓') : ''}
        </button>
      ) : (
        <span className="h plain">Name</span>
      )}
      {visibleColumns(spec, visible).map((column) =>
        onSort && column.sortKey ? (
          <button
            key={column.id}
            type="button"
            className={`h${column.align === 'right' ? ' right' : ''}${sortColumn === column.sortKey ? ' active' : ''}`}
            onClick={() => onSort(column.sortKey!)}
            aria-label={`Sort by ${column.label}`}
          >
            {column.label} {sortColumn === column.sortKey ? (order === 'asc' ? '↑' : '↓') : ''}
          </button>
        ) : (
          <span key={column.id} className={`h plain${column.align === 'right' ? ' right' : ''}`}>
            {column.label}
          </span>
        ),
      )}
    </div>
  )
}

// ---------- Location / state helpers ----------

// The copy's storage position, one short fact: S3 state, a named
// secondary location, or the default local volume.
function locationState(model: LocalModel): string {
  if (model.storage_backend === 's3') return model.cached ? 'S3 cached' : 'S3 only'
  if (model.location && model.location !== 'primary') return model.location
  return 'local'
}

function rowKeyDown(onOpen: () => void) {
  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onOpen()
    }
  }
}

// ---------- Cards (treatment A) ----------

// The hub's card: serif name, one mono fact line (params / library,
// omitted when unknown), bottom-pinned kind line with the library
// state, hairline footer with reach numbers and the two quiet actions.
interface HubRowProps {
  model: HubModel
  onOpen: (repoId: string) => void
  onDownload: (repoId: string) => void
  onSave: (model: HubModel) => void
  queuing?: boolean
  saving?: boolean
}

export function HubModelRow({
  model,
  onOpen,
  onDownload,
  onSave,
  queuing,
  saving,
}: HubRowProps) {
  const owner = familyOf(model.id)
  const name = owner === model.id ? model.id : model.id.slice(owner.length + 1)
  return (
    <article
      className="matrix-card"
      onClick={() => onOpen(model.id)}
      tabIndex={0}
      role="button"
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(model.id)
        }
      }}
      aria-label={`Open ${model.id} model details`}
    >
      <h3 className="mname">
        {owner !== model.id && <span className="org">{owner}/</span>}
        {name || model.id}
      </h3>
      {(() => {
        // Glance line: quants when the repo has them (else format),
        // params when the Hub knows them, and when the repo changed.
        // Anything unknown is omitted, never guessed.
        const head = model.quant_count
          ? `${model.quant_count} quant${model.quant_count === 1 ? '' : 's'}`
          : model.library_name || ''
        const facts = [
          head,
          model.parameter_count ? formatParameterCount(model.parameter_count) : '',
          model.last_modified ? relativeTime(model.last_modified) : '',
        ].filter((part) => part !== '')
        if (facts.length === 0) return null
        return (
          <div className="mfacts">
            <span className="accent">{facts[0]}</span>
            {facts.slice(1).map((fact) => (
              <span key={fact}> / {fact}</span>
            ))}
          </div>
        )
      })()}
      <div className="mkind">
        <span>{model.pipeline_tag ? taskLabel(model.pipeline_tag) : '—'}</span>
        {model.local ? (
          <span className="k-state state ok">
            <Check size={12} /> In library
          </span>
        ) : model.gated ? (
          <span className="k-state">
            <LockKeyhole size={11} /> Gated
          </span>
        ) : (
          <span className="k-state">Not in library</span>
        )}
      </div>
      <div className="mfoot">
        <span>
          {formatNumber(model.downloads)} dl / {formatNumber(model.likes)} likes
        </span>
        <span className="mfoot-actions">
          <button
            type="button"
            className="icon-btn"
            disabled={queuing}
            title={model.local ? 'Update this copy' : 'Download the full repository'}
            aria-label={model.local ? `Update ${model.id}` : `Download the full repository for ${model.id}`}
            onClick={(event) => {
              event.stopPropagation()
              onDownload(model.id)
            }}
          >
            {model.local ? (
              <RefreshCw size={13} />
            ) : queuing ? (
              <LoaderCircle size={13} className="spin" />
            ) : (
              <Download size={13} />
            )}
          </button>
          <button
            type="button"
            className={`icon-btn${model.saved ? ' on' : ''}`}
            disabled={saving}
            aria-label={model.saved ? `Remove ${model.id} from saved models` : `Save ${model.id}`}
            title={model.saved ? 'Remove from saved models' : 'Save for later'}
            onClick={(event) => {
              event.stopPropagation()
              onSave(model)
            }}
          >
            {saving ? (
              <RefreshCw size={13} className="spin" />
            ) : (
              <Heart size={13} fill={model.saved ? 'currentColor' : 'none'} />
            )}
          </button>
        </span>
      </div>
    </article>
  )
}

interface LocalRowProps {
  model: LocalModel
  onOpen: (repoId: string) => void
  updateAvailable?: boolean
}

// The library's card: copy facts as one mono line (size / library,
// params once captured), the pinned-or-latest note as one quiet line,
// kind with the state bottom-pinned — upstream drift wins over the
// storage position, the same priority as the rows — and freshness with
// the file count in the hairline footer.
export function LocalMatrixCard({ model, onOpen, updateAvailable }: LocalRowProps) {
  const owner = familyOf(model.repo_id)
  const name = owner === model.repo_id ? model.repo_id : model.repo_id.slice(owner.length + 1)
  return (
    <article
      className="matrix-card"
      onClick={() => onOpen(model.repo_id)}
      tabIndex={0}
      role="button"
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(model.repo_id)
        }
      }}
      aria-label={`Open ${model.repo_id} model details`}
    >
      <h3 className="mname">
        {owner !== model.repo_id && <span className="org">{owner}/</span>}
        {name}
      </h3>
      <div className="mfacts">
        <span className="accent">{formatBytes(model.size_bytes)}</span>
        {model.library_name ? ` / ${model.library_name}` : ''}
        {model.parameter_count ? ` / ${formatParameterCount(model.parameter_count)}` : ''}
      </div>
      {model.note_preview && (
        <p className="mnote" title={model.note_preview.body}>
          {model.note_preview.pinned && (
            <Pin size={10} className="pin-glyph" aria-hidden="true" />
          )}
          {model.note_preview.body}
        </p>
      )}
      <div className="mkind">
        <span>{model.pipeline_tag ? taskLabel(model.pipeline_tag) : '—'}</span>
        {updateAvailable ? (
          <span className="k-state state attn">
            <RefreshCw size={11} /> Update available
          </span>
        ) : (
          <span className="k-state">{locationState(model)}</span>
        )}
      </div>
      <div className="mfoot">
        <span>
          {model.managed ? 'Managed / ' : ''}
          Indexed {relativeTime(model.modified_at)} · {model.file_count}{' '}
          {model.file_count === 1 ? 'file' : 'files'}
        </span>
      </div>
    </article>
  )
}

// ---------- Rows (customizable columns) ----------

interface LocalRowFullProps {
  model: LocalModel
  onOpen: (repoId: string) => void
  visible: string[]
  updateAvailable?: boolean
  underFamily?: boolean
}

// The library's pro-surface row: name plus whichever columns the user
// keeps visible. State shows only what is actually known — a watched
// copy with drift — and stays blank rather than claiming "up to date"
// for copies nobody watches.
export function LocalModelRow({ model, onOpen, visible, updateAvailable, underFamily }: LocalRowFullProps) {
  const owner = familyOf(model.repo_id)
  const name = owner === model.repo_id ? model.repo_id : model.repo_id.slice(owner.length + 1)
  const cells: Record<string, ReactNode> = {
    kind: <span className="c-kind">{model.pipeline_tag ? taskLabel(model.pipeline_tag) : '—'}</span>,
    size: <span className="c-size">{formatBytes(model.size_bytes)}</span>,
    params: model.parameter_count ? (
      <span className="c-params">{formatParameterCount(model.parameter_count)}</span>
    ) : (
      <span className="c-empty">—</span>
    ),
    library: <span className="c-loc">{model.library_name || '—'}</span>,
    location: <span className="c-loc">{locationState(model)}</span>,
    state: updateAvailable ? (
      <span className="state attn">
        <RefreshCw size={11} /> Update available
      </span>
    ) : (
      <span className="c-empty">—</span>
    ),
    note: model.note_preview ? (
      <span className="c-note" title={model.note_preview.body}>
        {model.note_preview.pinned && <Pin size={10} className="pin-glyph" aria-hidden="true" />}
        {model.note_preview.body}
      </span>
    ) : (
      <span className="c-empty">—</span>
    ),
    modified: <span className="c-mod">{relativeTime(model.modified_at)}</span>,
    files: <span className="c-num">{model.file_count}</span>,
    downloaded: model.downloaded_at ? (
      <span className="c-mod">{relativeTime(model.downloaded_at)}</span>
    ) : (
      <span className="c-empty">—</span>
    ),
  }
  return (
    <div
      className={`row${underFamily ? ' under-family' : ''}`}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(model.repo_id)}
      onKeyDown={rowKeyDown(() => onOpen(model.repo_id))}
    >
      <span className="c-name">
        {owner !== model.repo_id && <span className="org">{owner}/</span>}
        {name}
      </span>
      {visibleColumns(LOCAL_COLUMNS, visible).map((column) => (
        <span key={column.id} className={column.align === 'right' ? 'right' : undefined}>
          {cells[column.id]}
        </span>
      ))}
    </div>
  )
}

interface HubRowFullProps {
  model: HubModel
  onOpen: (repoId: string) => void
  visible: string[]
}

export function HubModelRowFull({ model, onOpen, visible }: HubRowFullProps) {
  const owner = familyOf(model.id)
  const name = owner === model.id ? model.id : model.id.slice(owner.length + 1)
  const cells: Record<string, ReactNode> = {
    kind: <span className="c-kind">{model.pipeline_tag ? taskLabel(model.pipeline_tag) : '—'}</span>,
    params: model.parameter_count ? (
      <span className="c-params">{formatParameterCount(model.parameter_count)}</span>
    ) : (
      <span className="c-empty">—</span>
    ),
    quants: model.quant_count ? (
      <span className="c-num">{model.quant_count}</span>
    ) : (
      <span className="c-empty">—</span>
    ),
    library: <span className="c-loc">{model.library_name || '—'}</span>,
    state: model.local ? (
      <span className="state ok">
        <Check size={11} /> In library
      </span>
    ) : model.gated ? (
      <span className="state muted">
        <LockKeyhole size={11} /> Gated
      </span>
    ) : (
      <span className="c-empty">—</span>
    ),
    downloads: <span className="c-num">{formatNumber(model.downloads)}</span>,
    likes: <span className="c-num">{formatNumber(model.likes)}</span>,
    modified: <span className="c-mod">{relativeTime(model.last_modified)}</span>,
  }
  return (
    <div
      className="row"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(model.id)}
      onKeyDown={rowKeyDown(() => onOpen(model.id))}
    >
      <span className="c-name">
        {owner !== model.id && <span className="org">{owner}/</span>}
        {name || model.id}
      </span>
      {visibleColumns(HUB_COLUMNS, visible).map((column) => (
        <span key={column.id} className={column.align === 'right' ? 'right' : undefined}>
          {cells[column.id]}
        </span>
      ))}
    </div>
  )
}
