import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, Eye, LoaderCircle } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import type { HubModelDetails, LocalModelDetails, User, WatchRecord } from '../types'
import { LibraryConsole, LibrarySurface, ModelCardDocument, type SurfaceTab } from './SurfacePanels'
import { DownloadZone, WatchToggle } from './HubPanels'
import { NotesPanel } from './NotesPanel'
import { GgufInspector } from './GgufInspector'
import { formatBytes, formatParameterCount, relativeTime } from '../utils'

interface ModelPageProps {
  onQueued?: (repoId: string) => void
  onChanged?: () => void
  onToast: (message: string, tone?: 'success' | 'error') => void
  canManageRuntimes: boolean
  user: User
}

// The one model PAGE (M2, resumed as its true shape): every door links
// here — Explore, Library, Saved, Watched, download history — so the
// address bar holds a place and the browser's back button means back.
//
// Page reads, top to bottom: the header (name, state, watch) with a mono
// fact line; for a hub model, the download zone (mode presets beside a
// per-file table); for a copy, an ops line (state first, actions after)
// that opens a manage panel for verify / move / delete / runtime; notes,
// full width; then the reading surface (card, files, GGUF, updates).
export function ModelPage({
  onQueued,
  onChanged,
  onToast,
  canManageRuntimes,
  user,
}: ModelPageProps) {
  const params = useParams()
  const navigate = useNavigate()
  const repoId = useMemo(() => {
    // The route carries the two halves of a repo id separately; ids are
    // owner/name. react-router decodes params, so no decode here.
    const owner = params.owner || ''
    const name = params.name || ''
    return owner && name ? `${owner}/${name}` : ''
  }, [params.owner, params.name])

  const [model, setModel] = useState<HubModelDetails | null>(null)
  const [hubError, setHubError] = useState('')
  const [local, setLocal] = useState<LocalModelDetails | null>(null)
  // `local` doubles as the presence answer, so its null state cannot say
  // whether the probe is still running or already answered "not in the
  // library" — derive readiness from a settled flag instead. A probe
  // that fails (no copy) MUST settle, or hub-only pages hang forever
  // with nothing but the facts zone rendered.
  const [probeSettled, setProbeSettled] = useState(false)
  const [watchRecord, setWatchRecord] = useState<WatchRecord | null>(null)
  // D13 default: a download watches its repository. `watchTouched` splits
  // "the user explicitly turned the watch off" from "the toggle simply
  // reflects a not-yet-watched repo" - only consent reaches the queue.
  const [watchTouched, setWatchTouched] = useState(false)
  const [watchBusy, setWatchBusy] = useState(false)
  const [watchError, setWatchError] = useState('')
  const [tab, setTab] = useState<SurfaceTab>('card')
  const [revision, setRevision] = useState('main')
  const [managePanel, setManagePanel] = useState<'integrity' | 'delete' | 'runtime' | null>(null)
  const [manageIntent, setManageIntent] = useState<'verify' | 'move' | null>(null)
  const [autoCheckKey, setAutoCheckKey] = useState(0)

  const inLibrary = local !== null
  const watched = watchRecord !== null

  useEffect(() => {
    if (!repoId) {
      setModel(null)
      setHubError('')
      setLocal(null)
      setWatchRecord(null)
      return
    }
    let ignore = false
    setModel(null)
    setHubError('')
    setLocal(null)
    setProbeSettled(false)
    setWatchRecord(null)
    setTab('card')
    setRevision('main')
    setWatchTouched(false)
    setWatchBusy(false)
    setWatchError('')
    setManagePanel(null)
    setManageIntent(null)
    setAutoCheckKey(0)
    api
      .modelDetails(repoId)
      .then((payload) => {
        if (!ignore) setModel(payload)
      })
      .catch((reason) => {
        if (!ignore) setHubError(reason instanceof Error ? reason.message : 'Unable to load the repository.')
      })
    api
      .localModelDetails(repoId)
      .then((payload) => {
        if (!ignore) {
          setLocal(payload)
          setProbeSettled(true)
        }
      })
      .catch(() => {
        if (!ignore) setProbeSettled(true)
      })
    api
      .watches()
      .then((payload) => {
        if (!ignore) setWatchRecord(payload.items.find((item) => item.repo_id === repoId) || null)
      })
      .catch(() => undefined)
    return () => {
      ignore = true
    }
  }, [repoId])

  // Back is the close button now. Escape mirrors it, but only when no
  // form control has the focus — Escape closes selects and clears
  // composers, and it may not grab that job.
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      const focus = document.activeElement
      if (focus && ['INPUT', 'TEXTAREA', 'SELECT'].includes(focus.tagName)) return
      navigate(-1)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [navigate])

  const ggufFiles = useMemo(
    () => model?.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')) || [],
    [model],
  )

  async function toggleWatch() {
    if (!repoId || watchBusy) return
    setWatchBusy(true)
    setWatchError('')
    try {
      if (watched) {
        await api.unwatchModel(repoId)
        setWatchRecord(null)
        if (inLibrary) {
          onToast(`${repoId} is no longer watched — this copy stays where it is.`)
        }
      } else {
        await api.watchModel(repoId)
        if (inLibrary) {
          onToast(`${repoId} is now watched.`)
        }
      }
      setWatchTouched(true)
    } catch (reason) {
      setWatchError(reason instanceof Error ? reason.message : 'Unable to change the watch.')
    } finally {
      setWatchBusy(false)
    }
  }

  // The watch row settles from the API list, but the toggle is the
  // instant truth for this page visit; refetch once the toggle resolves.
  useEffect(() => {
    if (!watchTouched || !repoId) return
    let ignore = false
    api
      .watches()
      .then((payload) => {
        if (!ignore) setWatchRecord(payload.items.find((item) => item.repo_id === repoId) || null)
      })
      .catch(() => undefined)
    return () => {
      ignore = true
    }
  }, [watchTouched, repoId, watched, watchBusy])

  // Library actions refresh the copy facts in place, so the pinned
  // fact lines stay truthful after a move, a cache swap, or a deletion.
  function refreshDetails(target: string) {
    api
      .localModelDetails(target)
      .then((payload) => setLocal(payload))
      .catch(() => undefined)
  }

  function openManage(panel: 'integrity' | 'delete' | 'runtime', intent: 'verify' | 'move' | null = null) {
    if (managePanel === panel) {
      setManagePanel(null)
      return
    }
    setManagePanel(panel)
    setManageIntent(panel === 'integrity' ? intent : null)
  }

  function reviewDiff() {
    setTab('updates')
    setAutoCheckKey((key) => key + 1)
  }

  if (!repoId) {
    return (
      <div className="standard-page model-page">
        <div className="empty-state spacious">
          <h2>No repository here</h2>
          <p>The address does not name a model.</p>
        </div>
      </div>
    )
  }

  const [org, modelName] = repoId.split('/', 2)
  const drift = inLibrary && watchRecord?.update_available === true

  return (
    <div className="standard-page model-page">
      <header className="model-header">
        <div className="model-title-row">
          <h1><span className="org">{org}/</span>{modelName}</h1>
          <div className="title-actions">
            {probeSettled && (inLibrary
              ? <span className="state ok"><span className="glyph" aria-hidden="true">✓</span>In library</span>
              : <span className="state ok"><Eye size={13} aria-hidden="true" />Not in library</span>)}
            {(model || local) && <WatchToggle watched={watched} busy={watchBusy} onToggle={toggleWatch} />}
          </div>
        </div>
        {(model || local) && (
          <div className="meta-line model-meta">
            {model?.parameter_count ? <span>{formatParameterCount(model.parameter_count)} params</span> : null}
            {inLibrary && local ? (
              <>
                {model?.parameter_count ? <span className="sep">/</span> : null}
                <span>{formatBytes(local.model.size_bytes)} on disk</span>
                <span className="sep">/</span>
                <span>
                  {local.model.cached
                    ? `/models/${local.model.relative_path}`
                    : local.model.remote_uri}
                </span>
                <span className="sep">/</span>
                <span>{local.model.sha ? `rev ${local.model.sha.slice(0, 7)} pinned` : 'no pinned revision'}</span>
                {local.model.storage_backend === 's3' ? (
                  <>
                    <span className="sep">/</span>
                    <span>{local.model.cached ? 'S3, cached locally' : 'S3 only'}</span>
                  </>
                ) : null}
                <span className="sep">/</span>
                <span>indexed {relativeTime(local.model.modified_at)}</span>
                <span className="sep">/</span>
                <a
                  className="m-link"
                  href={model?.source_url || local.model.source_url || `https://huggingface.co/${repoId}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  huggingface.co/{repoId} <ExternalLink size={11} />
                </a>
              </>
            ) : model ? (
              <>
                {model.parameter_count ? <span className="sep">/</span> : null}
                <span>{new Intl.NumberFormat().format(model.downloads)} dl/mo</span>
                <span className="sep">/</span>
                <span>{new Intl.NumberFormat().format(model.likes)} likes</span>
                <span className="sep">/</span>
                <span>{formatBytes(model.total_bytes)} repository</span>
                <span className="sep">/</span>
                <span>rev <code>{model.revision}</code></span>
                {model.last_modified ? (
                  <>
                    <span className="sep">/</span>
                    <span>updated {relativeTime(model.last_modified)}</span>
                  </>
                ) : null}
                <span className="sep">/</span>
                <a className="m-link" href={model.source_url} target="_blank" rel="noreferrer">
                  huggingface.co/{repoId} <ExternalLink size={11} />
                </a>
              </>
            ) : null}
          </div>
        )}
      </header>

      {!model && !hubError && (
        <div className="busy-line">
          <LoaderCircle size={24} className="spin" /> Reading repository metadata…
        </div>
      )}
      {hubError && <div className="inline-error">{hubError}</div>}
      {watchError && <div className="inline-error">{watchError}</div>}

      {inLibrary && local && (
        <div className="ops-line">
          {drift && watchRecord && (
            <span className="state attn">
              <span className="glyph" aria-hidden="true">↻</span>
              Update available
              {watchRecord.checked_at ? `, checked ${relativeTime(watchRecord.checked_at)}` : ''}
            </span>
          )}
          <span className="ops-actions">
            <button type="button" className="download-button compact" onClick={reviewDiff}>Review diff</button>
            <button type="button" className="secondary-button compact" onClick={() => openManage('integrity', 'verify')}>Verify</button>
            <button type="button" className="secondary-button compact" onClick={() => openManage('integrity', 'move')}>Move…</button>
            <button type="button" className="secondary-button compact danger" onClick={() => openManage('delete')}>Delete…</button>
            {canManageRuntimes && (
              <button type="button" className="secondary-button compact" onClick={() => openManage('runtime')}>Runtime…</button>
            )}
          </span>
        </div>
      )}

      {inLibrary && local && managePanel && (
        <div className="manage-panel">
          <LibraryConsole
            repoId={repoId}
            details={local}
            refreshDetails={refreshDetails}
            onChanged={onChanged}
            onToast={onToast}
            onDeleted={() => {
              onChanged?.()
              navigate('/local')
            }}
            canManageRuntimes={canManageRuntimes}
            panel={managePanel}
            intent={manageIntent}
          />
        </div>
      )}

      {model && !inLibrary && (
        <DownloadZone
          repoId={repoId}
          model={model}
          onQueued={onQueued || (() => undefined)}
          revision={revision}
          onRevisionChange={setRevision}
          watched={watched}
          watchTouched={watchTouched}
        />
      )}

      {inLibrary && local && (
        <NotesPanel repoId={repoId} user={user} onToast={onToast} />
      )}

      {(model || inLibrary) && (
        <>
          <div className="surface-tabs" role="tablist" aria-label="Model surface">
            <button
              id="surface-card-tab"
              role="tab"
              aria-selected={tab === 'card'}
              aria-controls="surface-card-panel"
              className={tab === 'card' ? 'active' : ''}
              onClick={() => setTab('card')}
            >
              Card
            </button>
            <button
              id="surface-files-tab"
              role="tab"
              aria-selected={tab === 'files'}
              aria-controls="surface-files-panel"
              className={tab === 'files' ? 'active' : ''}
              onClick={() => setTab('files')}
            >
              Files <span>{inLibrary ? local?.model.file_count : model?.files.length}</span>
            </button>
            {model && ggufFiles.length > 0 && (
              <button
                id="surface-gguf-tab"
                role="tab"
                aria-selected={tab === 'gguf'}
                aria-controls="surface-gguf-panel"
                className={tab === 'gguf' ? 'active' : ''}
                onClick={() => setTab('gguf')}
              >
                GGUF <span>{ggufFiles.length}</span>
              </button>
            )}
            {inLibrary && (
              <button
                id="surface-updates-tab"
                role="tab"
                aria-selected={tab === 'updates'}
                aria-controls="surface-updates-panel"
                className={tab === 'updates' ? 'active' : ''}
                onClick={() => setTab('updates')}
              >
                Updates
              </button>
            )}
          </div>
          <div className="surface-panels">
            <div id="surface-card-panel" hidden={tab !== 'card' || inLibrary}>
              {model?.model_card ? (
                <ModelCardDocument
                  source={model.model_card}
                  sourceUrl={model.source_url}
                  revision={model.revision}
                />
              ) : (
                <div className="empty-compact">
                  {model
                    ? 'This repository does not expose a README model card.'
                    : 'The upstream repository could not be read; the stored copy still answers below.'}
                </div>
              )}
            </div>
            <div id="surface-files-panel" hidden={tab !== 'files' || inLibrary}>
              <div className="file-list">
                {model?.files.map((file) => (
                  <div key={file.path} className="file-row">
                    <span className="file-glyph" aria-hidden="true">▸</span>
                    <span title={file.path}>{file.path}</span>
                    <small>{file.size ? formatBytes(file.size) : '—'}</small>
                  </div>
                ))}
              </div>
            </div>
            {model && (
              <div id="surface-gguf-panel" hidden={tab !== 'gguf'}>
                <GgufInspector repoId={repoId} revision={revision} files={ggufFiles} />
              </div>
            )}
            {inLibrary && local && (
              <LibrarySurface
                repoId={repoId}
                details={local}
                tab={tab}
                onToast={onToast}
                onChanged={onChanged}
                autoCheckKey={autoCheckKey}
              />
            )}
          </div>
        </>
      )}
    </div>
  )
}

