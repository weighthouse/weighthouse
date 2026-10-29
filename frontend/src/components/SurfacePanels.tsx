import 'katex/dist/katex.min.css'
import { Children, isValidElement, memo, useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import {
  AlertTriangle,
  Check,
  CloudDownload,
  Copy,
  Download,
  ExternalLink,
  File,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  Rocket,
  Server,
  ShieldCheck,
  Trash2,
} from 'lucide-react'
import { api } from '../api'
import {
  modelCardHeadingId,
  modelCardSanitizeSchema,
  prepareModelCardMarkdown,
  resolveModelCardUrl,
} from '../modelCard'
import type {
  LocalCardSnapshot,
  LocalModelDetails,
  RuntimeJob,
  RuntimeTarget,
  StorageLocationInfo,
  VerifyReport,
} from '../types'
import {
  UPDATE_STATUS_LABELS,
  applyableUpdateRows,
  reportOnlyUpdateRows,
  transferBytesOf,
  updateSummaryLine,
  type UpdateDiff,
} from '../updateDiff.ts'
import { copyDeletionConfirmed, deletableRows, freedBytesOf, type DeletionPlan } from '../deletion.ts'
import { formatBytes } from '../utils'

// Download-mode pattern semantics are owned by the backend planner
// (backend/app/planning.py); this module only renders mode labels and
// per-file display filtering. The model page keeps the actions in its
// console column: LibrarySurface renders the reading panels of a copy
// (card, files, updates) beside that column, and LibraryConsole renders
// the actions themselves (verify, move, delete, cache, and runtime
// dispatch; the watch toggle is drawn by the page itself).

function childText(children: ReactNode): string {
  return Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child)
      if (isValidElement<{ children?: ReactNode }>(child)) return childText(child.props.children)
      return ''
    })
    .join('')
}

function scrollToCardHeading(event: React.MouseEvent<HTMLAnchorElement>, href: string) {
  event.preventDefault()
  let targetId = href.slice(1)
  try {
    targetId = decodeURIComponent(targetId)
  } catch {
    // Keep the literal fragment when a model card contains malformed escaping.
  }
  const documentRoot = event.currentTarget.closest('.model-card-document')
  const target = Array.from(documentRoot?.querySelectorAll<HTMLElement>('[id]') || []).find(
    (element) => element.id === targetId,
  )
  target?.scrollIntoView({ block: 'start' })
}

const modelCardComponents: Components = {
  h1: ({ node: _node, children, ...props }) => (
    <h1 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h1>
  ),
  h2: ({ node: _node, children, ...props }) => (
    <h2 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h2>
  ),
  h3: ({ node: _node, children, ...props }) => (
    <h3 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h3>
  ),
  h4: ({ node: _node, children, ...props }) => (
    <h4 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h4>
  ),
  h5: ({ node: _node, children, ...props }) => (
    <h5 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h5>
  ),
  h6: ({ node: _node, children, ...props }) => (
    <h6 {...props} id={modelCardHeadingId(childText(children)) || undefined}>
      {children}
    </h6>
  ),
  a: ({ node: _node, href, children, ...props }) => {
    const isHeadingLink = href?.startsWith('#') || false
    const showExternalIcon =
      !isHeadingLink && /^https?:/i.test(href || '') && childText(children).trim().length > 0
    return (
      <a
        {...props}
        href={href}
        target={isHeadingLink ? undefined : '_blank'}
        rel={isHeadingLink ? undefined : 'noreferrer noopener'}
        onClick={isHeadingLink && href ? (event) => scrollToCardHeading(event, href) : undefined}
      >
        {children}
        {showExternalIcon && <ExternalLink size={11} aria-hidden="true" />}
      </a>
    )
  },
  img: ({ node: _node, className, src, alt, ...props }) => {
    const badge =
      typeof src === 'string' &&
      /(?:img\.shields\.io|badge(?:s)?[./_-]|colab-badge)/i.test(src)
    const classes = [className, badge ? 'model-card-badge' : ''].filter(Boolean).join(' ')
    return (
      <img
        {...props}
        className={classes || undefined}
        src={src}
        alt={alt || ''}
        loading="lazy"
        decoding="async"
      />
    )
  },
}

export const ModelCardDocument = memo(function ModelCardDocument({
  source,
  sourceUrl,
  revision,
}: {
  source: string
  sourceUrl: string
  revision: string
}) {
  const preparedSource = useMemo(() => prepareModelCardMarkdown(source), [source])
  return (
    <article className="model-card-document">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: false }]]}
        rehypePlugins={[
          rehypeRaw,
          [rehypeSanitize, modelCardSanitizeSchema],
          rehypeKatex,
        ]}
        urlTransform={(url, attribute) => {
          const resolved = resolveModelCardUrl(url, attribute, sourceUrl, revision)
          return resolved === null ? null : defaultUrlTransform(resolved)
        }}
        components={modelCardComponents}
      >
        {preparedSource}
      </ReactMarkdown>
    </article>
  )
})

export type SurfaceTab = 'card' | 'files' | 'gguf' | 'updates'

// The reading panels of a pinned-revision copy. Every panel is driven by
// the revision recorded on disk, so the card and the file list describe
// what the library actually holds, not the Hub's current state.
export function LibrarySurface({
  repoId,
  details,
  tab,
  onToast,
  onChanged,
  autoCheckKey = 0,
}: {
  repoId: string
  details: LocalModelDetails
  tab: SurfaceTab
  onToast: (message: string, tone?: 'success' | 'error') => void
  onChanged?: () => void
  // Bumped by the ops line's "Review diff": the updates panel then runs
  // one upstream check on its own, so the button means "go look".
  autoCheckKey?: number
}) {
  const [card, setCard] = useState<LocalCardSnapshot | null>(null)
  const [diff, setDiff] = useState<UpdateDiff | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [updatePhase, setUpdatePhase] = useState<'idle' | 'checking' | 'ready' | 'applying'>('idle')
  const [updateError, setUpdateError] = useState('')
  const [lastAutoCheck, setLastAutoCheck] = useState(0)

  // One upstream check per ops-line intent; a fresh tab open does not
  // re-check (the button is the trigger, not the page visit).
  useEffect(() => {
    if (autoCheckKey === 0 || lastAutoCheck === autoCheckKey || !details.model.cached) return
    setLastAutoCheck(autoCheckKey)
    checkForUpdates()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoCheckKey, details.model.cached])

  useEffect(() => {
    let ignore = false
    setCard(null)
    setDiff(null)
    setPicked(new Set())
    setUpdatePhase('idle')
    setUpdateError('')
    if (!repoId) return
    api
      .localModelCard(repoId)
      .then((payload) => {
        if (!ignore) setCard(payload)
      })
      .catch(() => undefined)
    return () => {
      ignore = true
    }
  }, [repoId])

  async function checkForUpdates() {
    setUpdatePhase('checking')
    setUpdateError('')
    try {
      const nextDiff = await api.localModelUpdates(repoId)
      setDiff(nextDiff)
      setPicked(new Set(applyableUpdateRows(nextDiff).map((row) => row.path)))
      setUpdatePhase('ready')
    } catch (reason) {
      setDiff(null)
      setPicked(new Set())
      setUpdatePhase('idle')
      setUpdateError(reason instanceof Error ? reason.message : 'Could not check for updates.')
    }
  }

  async function applyUpdates() {
    if (!diff || picked.size === 0) return
    setUpdatePhase('applying')
    setUpdateError('')
    try {
      await api.applyLocalModelUpdates(repoId, [...picked])
      onToast(`Update queued for ${repoId}.`)
      onChanged?.()
      setDiff(null)
      setPicked(new Set())
      setUpdatePhase('idle')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not queue the update.'
      setUpdateError(message)
      setUpdatePhase('ready')
      onToast(message, 'error')
    }
  }

  function togglePicked(path: string, checked: boolean) {
    setPicked((current) => {
      const next = new Set(current)
      if (checked) next.add(path)
      else next.delete(path)
      return next
    })
  }

  return (
    <>
      <div hidden={tab !== 'card'}>
        <section className="updates-section card-section">
          <div className="updates-head">
            <div>
              <strong>Model card</strong>
              <p>
                {card?.sha
                  ? (
                    <>
                      Pinned to revision <code>{card.sha.slice(0, 7)}</code> — the version on disk.
                    </>
                  )
                  : 'No pinned upstream revision for this copy.'}
              </p>
            </div>
            {details.model.source_url && card?.sha && (
              <span className="card-links">
                <a
                  className="text-link"
                  href={`${details.model.source_url}/tree/${card.sha}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Tree <ExternalLink size={12} />
                </a>
                <a
                  className="text-link"
                  href={`${details.model.source_url}/blob/${card.sha}/README.md`}
                  target="_blank"
                  rel="noreferrer"
                >
                  README <ExternalLink size={12} />
                </a>
              </span>
            )}
          </div>
          {card?.card ? (
            <div className="card-document">
              <ModelCardDocument
                source={card.card}
                sourceUrl={details.model.source_url || ''}
                revision={card.sha || 'main'}
              />
            </div>
          ) : card?.sha ? (
            <p className="card-note">The card for this revision could not be read right now.</p>
          ) : (
            <p className="card-note">
              This copy has no upstream revision pinned (a local upload), so there is no card
              to show.
            </p>
          )}
        </section>
      </div>
      <div hidden={tab !== 'updates'}>
        {details.model.cached && (
          <section
            className={`updates-section diff-panel${diff?.updates_available ? ' warn' : ''}`}
          >
            {diff && diff.updates_available ? (
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
                  disabled={updatePhase === 'applying' || picked.size === 0}
                  onClick={applyUpdates}
                >
                  {updatePhase === 'applying'
                    ? <LoaderCircle size={15} className="spin" />
                    : <Download size={15} />}
                  Apply selected <small>{formatBytes(transferBytesOf(applyableUpdateRows(diff).filter((row) => picked.has(row.path))))}</small>
                </button>
              </div>
            ) : (
              <div className="updates-head">
                <button
                  type="button"
                  className="secondary-button compact"
                  disabled={updatePhase === 'checking' || updatePhase === 'applying'}
                  onClick={checkForUpdates}
                >
                  {updatePhase === 'checking'
                    ? <LoaderCircle size={14} className="spin" />
                    : <RefreshCw size={14} />}
                  {updatePhase === 'checking' ? 'Checking…' : 'Check for updates'}
                </button>
              </div>
            )}
            {updateError && <div className="inline-error">{updateError}</div>}
            {diff && !diff.updates_available && (
              <p className="updates-cleared">
                <span className="state ok">
                  <span className="glyph" aria-hidden="true">✓</span>
                  Up to date with <code>{diff.upstream.sha.slice(0, 7)}</code>.
                </span>
              </p>
            )}
            {diff && diff.updates_available && (
              <div className="file-table diff-table">
                <div className="ft-head diff-head-row">
                  <span />
                  <span className="diff-glyph-head" />
                  <span>File</span>
                  <span className="ft-size-head">On disk</span>
                  <span className="ft-size-head">Upstream</span>
                </div>
                <div className="ft-scroll">
                  {applyableUpdateRows(diff).map((row) => (
                    <label key={row.path} className="ft-row diff-row" title={UPDATE_STATUS_LABELS[row.status]}>
                      <input
                        type="checkbox"
                        checked={picked.has(row.path)}
                        onChange={(event) => togglePicked(row.path, event.target.checked)}
                      />
                      <span
                        className={`diff-glyph ${row.status}`}
                        aria-hidden="true"
                      >
                        {row.status === 'added' ? '+' : row.status === 'changed' ? '~' : '↻'}
                      </span>
                      <span className="ft-path" title={row.path}>{row.path}</span>
                      <span className="ft-size">{row.status === 'added' ? '—' : formatBytes(row.local.size)}</span>
                      <span className="ft-size">{formatBytes(row.upstream?.size || 0)}</span>
                    </label>
                  ))}
                  {reportOnlyUpdateRows(diff).map((row) => (
                    <div key={row.path} className="ft-row diff-row removed" title={UPDATE_STATUS_LABELS.removed}>
                      <span />
                      <span className="diff-glyph removed" aria-hidden="true">−</span>
                      <span className="ft-path" title={row.path}>{row.path}</span>
                      <span className="ft-size">{formatBytes(row.local.size)}</span>
                      <span className="ft-size">—</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {diff && diff.updates_available && reportOnlyUpdateRows(diff).length > 0 && (
              <p className="updates-note">
                Removed upstream — kept locally until you delete it.
              </p>
            )}
            {diff?.added_reporting_limited && diff.updates_available && (
              <p className="updates-note">
                This copy predates per-file tracking, so only metadata additions are reported. Re-download it to track weight additions.
              </p>
            )}
          </section>
        )}
      </div>
      <div hidden={tab !== 'files'}>
        <div className="file-list local-files">
          {details.files.map((file) => (
            <div key={file.path} className="file-row">
              {file.unsafe_serialization ? <AlertTriangle size={15} /> : <File size={15} />}
              <span title={file.path}>{file.path}</span>
              <small>{formatBytes(file.size)}</small>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

// The console column's manage controls for a copy: verify, move, cache,
// remove, and runtime dispatch. Rendered alongside the reading panels so
// they stay visible while the card or the file list is being read.
// The copy's manage controls, one panel open at a time. The ops line
// above decides which panel is open (integrity / delete / runtime);
// "Verify" and "Move…" arrive as intents so the right control acts at
// once. Everything the console column held lives here, full width.
export function LibraryConsole({
  repoId,
  details,
  refreshDetails,
  onChanged,
  onToast,
  onDeleted,
  canManageRuntimes,
  panel,
  intent = null,
}: {
  repoId: string
  details: LocalModelDetails
  refreshDetails: (repoId: string) => void
  onChanged?: () => void
  onToast: (message: string, tone?: 'success' | 'error') => void
  onDeleted?: () => void
  canManageRuntimes: boolean
  panel: 'integrity' | 'delete' | 'runtime' | null
  intent?: 'verify' | 'move' | null
}) {
  const [error, setError] = useState('')
  const [changingCache, setChangingCache] = useState(false)
  const [deletion, setDeletion] = useState<DeletionPlan | null>(null)
  const [deletePicked, setDeletePicked] = useState<Set<string>>(new Set())
  const [deletePhase, setDeletePhase] = useState<'idle' | 'loading' | 'ready'>('idle')
  const [deleteConfirm, setDeleteConfirm] = useState('')
  const [copiedId, setCopiedId] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [runtimeTargets, setRuntimeTargets] = useState<RuntimeTarget[]>([])
  const [runtimeTargetId, setRuntimeTargetId] = useState('')
  const [runtimeModelName, setRuntimeModelName] = useState('')
  const [runtimeSourceFile, setRuntimeSourceFile] = useState('')
  const [runtimeJob, setRuntimeJob] = useState<RuntimeJob | null>(null)
  const [dispatching, setDispatching] = useState(false)
  const [locations, setLocations] = useState<StorageLocationInfo[]>([])
  const [moveDestination, setMoveDestination] = useState('')
  const [moving, setMoving] = useState(false)
  const [moveError, setMoveError] = useState('')
  const [verifyReport, setVerifyReport] = useState<VerifyReport | null>(null)
  const [verifyPhase, setVerifyPhase] = useState<'idle' | 'checking' | 'ready'>('idle')
  const [verifyError, setVerifyError] = useState('')
  const currentLocation = details.model.location || 'primary'
  const moveCandidates = locations.filter((location) => location.name !== currentLocation)
  const selectedMoveDestination = moveDestination || moveCandidates[0]?.name || ''
  const activeRuntimeStatuses = ['queued', 'preparing', 'transferring', 'loading']

  useEffect(() => {
    setError('')
    setChangingCache(false)
    setDeletion(null)
    setDeletePicked(new Set())
    setDeletePhase('idle')
    setDeleteConfirm('')
    setDeleteError('')
    setRuntimeJob(null)
    setRuntimeModelName(repoId ? repoId.replace('/', '-').toLowerCase() : '')
    setRuntimeSourceFile('')
    setMoveDestination('')
    setMoveError('')
    setVerifyReport(null)
    setVerifyPhase('idle')
    setVerifyError('')
  }, [repoId])

  // The ops line opens this panel with an intent: "Verify" runs the
  // check immediately, "Move…" leaves the move select primed.
  useEffect(() => {
    if (!panel || !intent) return
    if (intent === 'verify' && panel === 'integrity' && verifyPhase === 'idle' && !verifyReport) {
      verifyCopy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, intent])

  useEffect(() => {
    if (!repoId || !canManageRuntimes) {
      setRuntimeTargets([])
      setRuntimeTargetId('')
      return
    }
    api
      .runtimeTargets()
      .then((payload) => {
        setRuntimeTargets(payload.items)
        setRuntimeTargetId((current) =>
          payload.items.some((target) => target.id === current)
            ? current
            : payload.items[0]?.id || '',
        )
      })
      .catch(() => setRuntimeTargets([]))
  }, [canManageRuntimes, repoId])

  useEffect(() => {
    if (details.model.storage_backend !== 'filesystem') {
      setLocations([])
      return
    }
    let ignore = false
    api
      .locations()
      .then((payload) => {
        if (!ignore) setLocations(payload.items)
      })
      .catch(() => {
        if (!ignore) setLocations([])
      })
    return () => {
      ignore = true
    }
  }, [details.model.storage_backend, details.model.repo_id])

  useEffect(() => {
    if (!runtimeJob || !activeRuntimeStatuses.includes(runtimeJob.status)) return
    const timer = window.setTimeout(() => {
      api
        .runtimeJob(runtimeJob.id)
        .then((next) => {
          setRuntimeJob(next)
          if (next.status === 'ready') {
            onToast(`${next.runtime_model_name} is ready on ${next.target_name}.`)
          } else if (next.status === 'failed') {
            onToast(next.error || 'Runtime load failed.', 'error')
          }
        })
        .catch(() => undefined)
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [onToast, runtimeJob])

  async function changeCache() {
    if (details.model.storage_backend !== 's3') return
    if (
      details.model.cached
      && !window.confirm(
        `Remove the local cache for ${details.model.repo_id}? The complete S3 copy will remain.`,
      )
    ) return
    setChangingCache(true)
    setError('')
    try {
      if (details.model.cached) {
        await api.evictLocalModelCache(details.model.repo_id)
        refreshDetails(details.model.repo_id)
        onToast(`${details.model.repo_id} is now stored in S3 only.`)
      } else {
        await api.restoreLocalModel(details.model.repo_id)
        refreshDetails(details.model.repo_id)
        onToast(`${details.model.repo_id} was restored to the local cache.`)
      }
      onChanged?.()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not update the model cache.'
      setError(message)
      onToast(message, 'error')
    } finally {
      setChangingCache(false)
    }
  }

  async function loadDeletionPlan() {
    setDeletePhase('loading')
    setDeleteError('')
    try {
      const plan = await api.localModelDeletionPlan(details.model.repo_id)
      setDeletion(plan)
      setDeletePicked(new Set())
      setDeletePhase('ready')
    } catch (reason) {
      setDeletion(null)
      setDeletePhase('idle')
      setDeleteError(reason instanceof Error ? reason.message : 'Could not inspect the stored files.')
    }
  }

  async function applyDeletion() {
    if (!deletion || deletePicked.size === 0) return
    setDeleteError('')
    try {
      const summary = await api.deleteLocalModelFiles(details.model.repo_id, [...deletePicked])
      onToast(
        `Removed ${summary.deleted.length} item${summary.deleted.length === 1 ? '' : 's'} and freed ${formatBytes(summary.freed_bytes)}.`,
      )
      setDeletion(null)
      setDeletePicked(new Set())
      setDeletePhase('idle')
      setDeleteConfirm('')
      refreshDetails(details.model.repo_id)
      onChanged?.()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not delete the selected files.'
      setDeleteError(message)
      onToast(message, 'error')
    }
  }

  async function verifyCopy() {
    setVerifyPhase('checking')
    setVerifyError('')
    try {
      const report = await api.verifyLocalModel(details.model.repo_id)
      setVerifyReport(report)
      setVerifyPhase('ready')
    } catch (reason) {
      setVerifyReport(null)
      setVerifyPhase('idle')
      setVerifyError(reason instanceof Error ? reason.message : 'Could not verify this copy.')
    }
  }

  async function moveCopy() {
    if (!selectedMoveDestination) return
    setMoving(true)
    setMoveError('')
    try {
      const result = await api.moveLocalModel(details.model.repo_id, selectedMoveDestination)
      onToast(`${details.model.repo_id} is now stored on ${result.moved_to}.`)
      refreshDetails(details.model.repo_id)
      setMoveDestination('')
      setVerifyReport(null)
      setVerifyPhase('idle')
      onChanged?.()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not move this model.'
      setMoveError(message)
      onToast(message, 'error')
    } finally {
      setMoving(false)
    }
  }

  function toggleDeletePicked(path: string, checked: boolean) {
    setDeletePicked((current) => {
      const next = new Set(current)
      if (checked) next.add(path)
      else next.delete(path)
      return next
    })
  }

  function setDeletePickedAll(checked: boolean) {
    if (!deletion) return
    setDeletePicked(checked ? new Set(deletableRows(deletion).map((row) => row.path)) : new Set())
  }

  async function copyRepoId() {
    try {
      await navigator.clipboard.writeText(details.model.repo_id)
      setCopiedId(true)
      window.setTimeout(() => setCopiedId(false), 1500)
    } catch {
      onToast('Unable to copy the repository id.', 'error')
    }
  }

  async function deleteWholeCopy() {
    if (!deletion) return
    if (!copyDeletionConfirmed(deletion, deleteConfirm)) return
    setDeleteError('')
    try {
      await api.deleteLocalModel(details.model.repo_id, deleteConfirm.trim())
      onToast(`${details.model.repo_id} was removed from the library.`)
      onChanged?.()
      onDeleted?.()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not delete this model.'
      setDeleteError(message)
      onToast(message, 'error')
    }
  }

  async function dispatchRuntime() {
    if (!runtimeTargetId || !runtimeModelName.trim()) return
    setDispatching(true)
    setError('')
    try {
      const job = await api.loadRuntime(runtimeTargetId, {
        repo_id: details.model.repo_id,
        runtime_model_name: runtimeModelName.trim(),
        ...(runtimeSourceFile ? { source_file: runtimeSourceFile } : {}),
      })
      setRuntimeJob(job)
      onToast(`${details.model.repo_id} was queued for ${job.target_name}.`)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'Could not send the model to the runtime.'
      setError(message)
      onToast(message, 'error')
    } finally {
      setDispatching(false)
    }
  }

  if (panel === null) return null

  return (
    <div className="manage-inner">
      {error && <div className="inline-error">{error}</div>}
      {panel === 'integrity' && details.model.storage_backend === 'filesystem' && (
        <section className="updates-section manage-section">
          <div className="updates-head">
            <div>
              <strong>Storage &amp; integrity</strong>
              <p>
                Currently <code>{currentLocation}</code>.
              </p>
            </div>
            <button
              type="button"
              className="secondary-button compact"
              disabled={verifyPhase === 'checking'}
              onClick={verifyCopy}
            >
              {verifyPhase === 'checking'
                ? <LoaderCircle size={14} className="spin" />
                : <ShieldCheck size={14} />}
              {verifyPhase === 'checking' ? 'Verifying…' : 'Verify copy'}
            </button>
          </div>
          {verifyError && <div className="inline-error">{verifyError}</div>}
          {verifyReport && verifyPhase === 'ready' && (
            <div className="updates-body">
              <p className="updates-summary">
                <span className={`update-status ${verifyReport.drifted ? 'changed' : 'added'}`}>
                  {verifyReport.drifted ? 'drifted' : 'intact'}
                </span>
                {' '}
                {verifyReport.summary.present} present
                {verifyReport.summary.missing > 0
                  ? `, ${verifyReport.summary.missing} missing`
                  : ''}
                {verifyReport.summary['size-differs'] > 0
                  ? `, ${verifyReport.summary['size-differs']} size-differed`
                  : ''}
                {'; disk holds '}
                {formatBytes(verifyReport.disk.size_bytes)} across{' '}
                {verifyReport.disk.file_count} files.
              </p>
              {verifyReport.files.filter((row) => row.status !== 'present').length > 0 && (
                <div className="update-rows">
                  {verifyReport.files
                    .filter((row) => row.status !== 'present')
                    .map((row) => (
                      <div key={row.path} className="update-row">
                        <span
                          className={`update-status ${row.status === 'missing' ? 'missing' : 'changed'}`}
                        >
                          {row.status === 'missing' ? 'missing' : 'size drift'}
                        </span>
                        <span className="update-path" title={row.path}>{row.path}</span>
                        <small>
                          {row.status === 'missing'
                            ? 'absent on disk'
                            : `${formatBytes(row.actual_size)} ≠ ${formatBytes(row.expected_size)}`}
                        </small>
                      </div>
                    ))}
                </div>
              )}
              {verifyReport.extras_beyond_manifest.length > 0 && (
                <>
                  <p className="updates-note">
                    {verifyReport.extras_beyond_manifest.length} untracked file
                    {verifyReport.extras_beyond_manifest.length === 1 ? '' : 's'} beyond
                    the manifest — your files, left exactly as they are.
                  </p>
                  <div className="update-rows">
                    {verifyReport.extras_beyond_manifest.slice(0, 12).map((item) => (
                      <div key={item.path} className="update-row">
                        <span className="update-status missing">extra</span>
                        <span className="update-path" title={item.path}>{item.path}</span>
                        <small>{formatBytes(item.size)}</small>
                      </div>
                    ))}
                    {verifyReport.extras_beyond_manifest.length > 12 && (
                      <div className="update-row">
                        <span className="update-status missing">extra</span>
                        <span className="update-path">
                          +{verifyReport.extras_beyond_manifest.length - 12} more untracked files
                        </span>
                        <small />
                      </div>
                    )}
                  </div>
                </>
              )}
              {verifyReport.repair && <p className="updates-note">{verifyReport.repair}</p>}
            </div>
          )}
          {moveCandidates.length > 0 ? (
            <div className="move-controls">
              <label>
                Move to location
                <select
                  value={selectedMoveDestination}
                  onChange={(event) => setMoveDestination(event.target.value)}
                >
                  {moveCandidates.map((location) => (
                    <option key={location.name} value={location.name}>
                      {location.name} ({location.root})
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="download-button"
                disabled={moving || !selectedMoveDestination}
                onClick={moveCopy}
              >
                {moving ? <LoaderCircle size={16} className="spin" /> : <HardDrive size={16} />}
                {moving ? 'Moving…' : `Move to ${selectedMoveDestination}`}
              </button>
            </div>
          ) : (
            <p className="updates-note move-note">
              {locations.length <= 1
                ? (
                  <>
                    This copy lives on <code>{currentLocation}</code>; declare additional
                    locations in <code>LOCATIONS_JSON</code> to move it.
                  </>
                )
                : 'No other storage location is available for this copy.'}
            </p>
          )}
          {moveError && <div className="inline-error">{moveError}</div>}
        </section>
      )}
      {panel === 'delete' && (
        <section className="updates-section deletion-section manage-section">
        <div className="updates-head">
          <div>
            <strong>Remove files</strong>
            <p>Free disk space or take this copy out of the library. Nothing is deleted without your approval.</p>
          </div>
          <button
            type="button"
            className="secondary-button compact"
            disabled={deletePhase === 'loading'}
            onClick={loadDeletionPlan}
          >
            {deletePhase === 'loading'
              ? <LoaderCircle size={14} className="spin" />
              : deletePhase === 'ready'
                ? <RefreshCw size={14} />
                : <Trash2 size={14} />}
            {deletePhase === 'loading' ? 'Inspecting…' : deletePhase === 'ready' ? 'Refresh' : 'Choose what to remove'}
          </button>
        </div>
        {deleteError && <div className="inline-error">{deleteError}</div>}
        {deletion && deletePhase === 'ready' && details.model.storage_backend !== 's3' && (
          <div className="updates-body">
            <div className="delete-pick-tools">
              <button type="button" className="secondary-button compact" onClick={() => setDeletePickedAll(true)}>All</button>
              <button type="button" className="secondary-button compact" onClick={() => setDeletePickedAll(false)}>None</button>
            </div>
            <div className="update-rows">
              {deletion.tracked.map((row) => (
                <label key={row.path} className="update-row">
                  <input
                    type="checkbox"
                    checked={deletePicked.has(row.path)}
                    onChange={(event) => toggleDeletePicked(row.path, event.target.checked)}
                  />
                  <span className="update-status">tracked</span>
                  <span className="update-path" title={row.path}>{row.path}</span>
                  <small>{formatBytes(row.size)}</small>
                </label>
              ))}
              {deletion.extras.map((row) => (
                <label key={row.path} className="update-row">
                  <input
                    type="checkbox"
                    checked={deletePicked.has(row.path)}
                    onChange={(event) => toggleDeletePicked(row.path, event.target.checked)}
                  />
                  <span className="update-status missing">leftover</span>
                  {row.kind === 'directory' ? (
                    <span className="update-path">{row.path} (resume cache)</span>
                  ) : (
                    <span className="update-path" title={row.path}>{row.path}</span>
                  )}
                  <small>{formatBytes(row.size)}</small>
                </label>
              ))}
            </div>
            <div className="updates-actions">
              <button
                type="button"
                className="cancel-download-button"
                disabled={deletePicked.size === 0}
                onClick={applyDeletion}
              >
                <Trash2 size={14} />
                {`Delete ${deletePicked.size} item${deletePicked.size === 1 ? '' : 's'} (${formatBytes(freedBytesOf(deletableRows(deletion).filter((row) => deletePicked.has(row.path))))})`}
              </button>
            </div>
          </div>
        )}
        <div className="delete-whole">
          <label>
            Type <code>{details.model.repo_id}</code> to remove the entire copy
            {details.model.storage_backend === 's3' ? ' including its durable S3 content' : ''}
            <input
              value={deleteConfirm}
              onChange={(event) => setDeleteConfirm(event.target.value)}
              placeholder={details.model.repo_id}
              spellCheck={false}
              autoComplete="off"
            />
          </label>
          <button type="button" className="secondary-button compact" onClick={copyRepoId}>
            {copiedId ? <Check size={13} /> : <Copy size={13} />}
            {copiedId ? 'Copied' : 'Copy'}
          </button>
          <button
            type="button"
            className="cancel-download-button"
            disabled={deleteConfirm.trim() !== details.model.repo_id}
            onClick={deleteWholeCopy}
          >
            <Trash2 size={14} /> Delete this model
          </button>
        </div>
      </section>
      )}
      {panel === 'integrity' && details.model.storage_backend === 's3' && (
        <div className="local-storage-actions manage-section">
          <button
            type="button"
            className="secondary-button"
            disabled={changingCache}
            onClick={changeCache}
          >
            {changingCache
              ? <LoaderCircle size={16} className="spin" />
              : details.model.cached ? <Trash2 size={16} /> : <CloudDownload size={16} />}
            {changingCache
              ? 'Working…'
              : details.model.cached ? 'Remove local cache' : 'Restore to local cache'}
          </button>
          <p>
            {details.model.cached
              ? 'The durable S3 copy stays available.'
              : 'Restore before loading this model in vLLM, llama.cpp, or another local runtime.'}
          </p>
        </div>
      )}
      {panel === 'runtime' && canManageRuntimes && runtimeTargets.length > 0 && (
        <section className="runtime-dispatch manage-section">
          <div className="runtime-dispatch-title">
            <Server size={17} />
            <div>
              <strong>Send to runtime</strong>
              <p>Transfer to Ollama or start this shared path through a vLLM agent.</p>
            </div>
          </div>
          <div className="runtime-dispatch-form">
            <label>
              Destination
              <select
                value={runtimeTargetId}
                onChange={(event) => {
                  setRuntimeTargetId(event.target.value)
                  setRuntimeSourceFile('')
                }}
              >
                {runtimeTargets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name} ({target.kind})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Served model name
              <input
                value={runtimeModelName}
                onChange={(event) => setRuntimeModelName(event.target.value)}
                maxLength={128}
              />
            </label>
            {runtimeTargets.find((target) => target.id === runtimeTargetId)?.kind === 'ollama'
              && details.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')).length > 1 && (
                <label className="runtime-source-field">
                  GGUF quantization
                  <select
                    value={runtimeSourceFile}
                    onChange={(event) => setRuntimeSourceFile(event.target.value)}
                  >
                    <option value="">Choose a GGUF file</option>
                    {details.files
                      .filter((file) => file.path.toLowerCase().endsWith('.gguf'))
                      .map((file) => (
                        <option key={file.path} value={file.path}>
                          {file.path} ({formatBytes(file.size)})
                        </option>
                      ))}
                  </select>
                </label>
              )}
            <button
              type="button"
              className="download-button"
              onClick={dispatchRuntime}
              disabled={
                dispatching
                || !details.model.cached
                || !runtimeTargetId
                || !runtimeModelName.trim()
                || (
                  runtimeTargets.find((target) => target.id === runtimeTargetId)?.kind === 'ollama'
                  && details.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')).length > 1
                  && !runtimeSourceFile
                )
                || Boolean(runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status))
              }
            >
              {dispatching || (runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status))
                ? <LoaderCircle size={16} className="spin" />
                : <Rocket size={16} />}
              {runtimeJob && activeRuntimeStatuses.includes(runtimeJob.status)
                ? runtimeJob.message
                : dispatching ? 'Queuing…' : 'Load model'}
            </button>
          </div>
          {runtimeJob && (
            <div className={`runtime-job-inline ${runtimeJob.status}`}>
              <div>
                <span>{runtimeJob.message}</span>
                <strong>{runtimeJob.progress.toFixed(0)}%</strong>
              </div>
              <div className="job-progress">
                <span style={{ width: `${runtimeJob.progress}%` }} />
              </div>
              {runtimeJob.error && <p>{runtimeJob.error}</p>}
            </div>
          )}
        </section>
      )}
      {panel === 'integrity' && (details.unsafe_file_count > 0 ? (
        <div className="security-note warning">
          <AlertTriangle size={16} />
          {details.unsafe_file_count} file{details.unsafe_file_count === 1 ? '' : 's'} may use
          pickle serialization. Do not load untrusted artifacts with code execution enabled.
        </div>
      ) : (
        <div className="security-note">
          <ShieldCheck size={16} />
          No common pickle-compatible file extensions found in the indexed file set.
        </div>
      ))}
    </div>
  )
}
