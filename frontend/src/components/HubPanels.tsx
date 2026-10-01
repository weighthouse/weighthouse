import { useEffect, useMemo, useState } from 'react'
import { Download, Eye, LoaderCircle } from 'lucide-react'
import { api } from '../api'
import type { DownloadMode, HubFile, HubModelDetails } from '../types'
import { formatBytes } from '../utils'

// Download-mode pattern semantics are owned by the backend planner
// (backend/app/planning.py, fnmatch over full paths); this module renders
// the mode presets and the per-file table, and reproduces fnmatch good
// enough to estimate a custom pattern selection client-side.

const downloadModes: Array<{
  id: DownloadMode
  label: string
}> = [
  { id: 'full', label: 'Full repository' },
  { id: 'safetensors', label: 'SafeTensors' },
  { id: 'gguf', label: 'One GGUF' },
  { id: 'metadata', label: 'Metadata only' },
  { id: 'custom', label: 'Custom' },
]

function isMetadataFile(file: HubFile): boolean {
  const name = file.path.toLowerCase().split('/').pop() || ''
  return (
    ['.json', '.md', '.txt', '.yaml', '.yml', '.jinja', '.model', '.tiktoken'].some((suffix) =>
      name.endsWith(suffix),
    ) ||
    name.startsWith('license') ||
    name.startsWith('tokenizer')
  )
}

// fnmatch -> RegExp for estimation only. fnmatch.fnmatch applies
// os.path.normcase (identity on POSIX), so no case folding here.
function globToRegExp(pattern: string): RegExp {
  let out = ''
  let i = 0
  while (i < pattern.length) {
    const ch = pattern[i]
    if (ch === '*') out += '.*'
    else if (ch === '?') out += '.'
    else if (ch === '[') {
      const close = pattern.indexOf(']', i + 1)
      if (close === -1) {
        out += '\\['
      } else {
        // fnmatch: [!seq] negates, as does [^seq]
        let body = pattern.slice(i + 1, close)
        if (body.startsWith('!')) body = '^' + body.slice(1)
        out += body.length ? `[${body}]` : '[^]]'
        i = close
      }
    } else {
      out += ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    }
    i += 1
  }
  return new RegExp(`^${out}$`)
}

function matchesPatterns(path: string, patterns: string[]): boolean {
  return patterns.some((pattern) => globToRegExp(pattern).test(path))
}

// The watch control, shared by both page states. The button says what it
// does; the header's state carries the rest.
export function WatchToggle({ watched, busy, onToggle }: {
  watched: boolean
  busy: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      className="secondary-button compact"
      onClick={onToggle}
      disabled={busy}
      aria-pressed={watched}
    >
      {busy ? <LoaderCircle size={13} className="spin" /> : <Eye size={13} />}
      {watched ? 'Watching' : 'Watch'}
    </button>
  )
}

// The roomy hub-state action zone: a wash panel holding the download
// decision (mode presets with size estimates, revision, patterns, safety)
// beside a per-file table. Modes are presets that fill the table; editing
// the checkboxes takes the selection over as a custom path list, which is
// exactly what the planner's include patterns accept.
export function DownloadZone({
  repoId,
  model,
  onQueued,
  revision,
  onRevisionChange,
  watched,
  watchTouched,
}: {
  repoId: string
  model: HubModelDetails
  onQueued: (repoId: string) => void
  revision: string
  onRevisionChange: (revision: string) => void
  watched: boolean
  watchTouched: boolean
}) {
  const [mode, setMode] = useState<DownloadMode>('full')
  const [ggufPath, setGgufPath] = useState('')
  const [excludeUnsafe, setExcludeUnsafe] = useState(false)
  const [include, setInclude] = useState('')
  const [exclude, setExclude] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [touched, setTouched] = useState(false)
  const [queuing, setQueuing] = useState(false)
  const [error, setError] = useState('')

  // A fresh page visit resets the form; the payload drives the
  // default quantization choice.
  useEffect(() => {
    setMode('full')
    setExcludeUnsafe(false)
    setInclude('')
    setExclude('')
    setTouched(false)
    setQueuing(false)
    setError('')
  }, [repoId])

  useEffect(() => {
    setGgufPath(model.files.find((file) => file.path.toLowerCase().endsWith('.gguf'))?.path || '')
  }, [model])

  const ggufFiles = useMemo(
    () => model.files.filter((file) => file.path.toLowerCase().endsWith('.gguf')),
    [model],
  )

  // The preset each mode implies, so a mode switch refills the table.
  const presetFor = useMemo(() => {
    const all = new Set(model.files.map((file) => file.path))
    if (mode === 'full') return all
    if (mode === 'safetensors') {
      return new Set(
        model.files
          .filter((file) => file.path.toLowerCase().endsWith('.safetensors') || isMetadataFile(file))
          .map((file) => file.path),
      )
    }
    if (mode === 'gguf') {
      const next = new Set(
        model.files
          .filter((file) => file.path === ggufPath || isMetadataFile(file))
          .map((file) => file.path),
      )
      return next
    }
    if (mode === 'metadata') {
      return new Set(model.files.filter(isMetadataFile).map((file) => file.path))
    }
    // custom: patterns, if any, else everything
    const inc = include.split(',').map((value) => value.trim()).filter(Boolean)
    const exc = exclude.split(',').map((value) => value.trim()).filter(Boolean)
    if (inc.length === 0 && exc.length === 0) return all
    return new Set(
      model.files
        .filter((file) =>
          (inc.length === 0 || matchesPatterns(file.path, inc))
          && !matchesPatterns(file.path, exc),
        )
        .map((file) => file.path),
    )
  }, [exclude, ggufPath, include, mode, model])

  // Presets refill the table until the user edits a checkbox themselves.
  useEffect(() => {
    if (!touched) setSelected(presetFor)
  }, [presetFor, touched])

  function toggleMode(id: DownloadMode) {
    setMode(id)
    setTouched(false)
  }

  function toggleFile(path: string, checked: boolean) {
    setTouched(true)
    setSelected((current) => {
      const next = new Set(current)
      if (checked) next.add(path)
      else next.delete(path)
      return next
    })
  }

  function selectAll() {
    setTouched(true)
    setSelected(new Set(model.files.map((file) => file.path)))
  }

  function selectNone() {
    setTouched(true)
    setSelected(new Set())
  }

  const effectiveSet = touched ? selected : presetFor
  const effectiveFiles = model.files.filter((file) => effectiveSet.has(file.path))
  const estimatedBytes = effectiveFiles.reduce((total, file) => total + (file.size || 0), 0)

  async function queue() {
    if (!repoId) return
    setQueuing(true)
    setError('')
    try {
      // Selection semantics live in the backend planner; the page sends
      // the mode and its parameters only. A checkbox-touched custom
      // selection ships as an exact include list (fnmatch literals).
      const patterns = include.split(',').map((value) => value.trim()).filter(Boolean)
      const exclusion = exclude.split(',').map((value) => value.trim()).filter(Boolean)
      await api.startDownload({
        repo_id: repoId,
        revision,
        mode: touched && mode !== 'custom' ? 'custom' : mode,
        gguf_path: mode === 'gguf' ? ggufPath : '',
        exclude_unsafe: excludeUnsafe,
        include_patterns:
          touched && mode !== 'custom'
            ? [...effectiveSet].sort()
            : mode === 'custom'
              ? patterns
              : [],
        exclude_patterns:
          mode === 'custom' && !touched ? exclusion : [],
        // D13: a download subscribes by default. Only an explicit decline
        // (the user toggled the watch in this page visit) keeps the
        // copy quiet; a pre-existing watch is left as it is.
        watch: watchTouched ? watched : true,
      })
      onQueued(repoId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to queue download')
    } finally {
      setQueuing(false)
    }
  }

  return (
    <div className="download-zone">
      <section className="wash-panel">
        <div>
          <h2>{model.local ? 'Update' : 'Download'}</h2>
          <p className="path-line">/models/{repoId}</p>
        </div>

        <label className="dz-field">
          Revision
          <input className="dz-input" value={revision} onChange={(event) => onRevisionChange(event.target.value)} />
        </label>

        <div className="mode-list" role="radiogroup" aria-label="Download contents">
          {downloadModes.map(({ id, label }) => {
            const unavailable = id === 'gguf' && ggufFiles.length === 0
            return (
              <button
                type="button"
                key={id}
                className={`mode-item${mode === id ? ' on' : ''}`}
                onClick={() => toggleMode(id)}
                disabled={unavailable}
                role="radio"
                aria-checked={mode === id}
              >
                <span className="m-radio" aria-hidden="true" />
                <span className="m-label">{label}</span>
                <span className="m-est">{unavailable ? 'no GGUF files' : formatBytes(presetEstimate(id))}</span>
              </button>
            )
          })}
        </div>

        {mode === 'gguf' && ggufFiles.length > 0 && (
          <label className="dz-field">
            Quantization file
            <select className="dz-select" value={ggufPath} onChange={(event) => setGgufPath(event.target.value)}>
              {ggufFiles.map((file) => (
                <option value={file.path} key={file.path}>
                  {file.path} ({formatBytes(file.size)})
                </option>
              ))}
            </select>
          </label>
        )}
        {mode === 'custom' && (
          <div className="field-pair">
            <label className="dz-field">
              <span>Include patterns <small>optional, comma separated</small></span>
              <input
                className="dz-input"
                value={include}
                onChange={(event) => { setInclude(event.target.value); setTouched(false) }}
                placeholder="*.safetensors, *.json"
              />
            </label>
            <label className="dz-field">
              <span>Exclude patterns <small>optional</small></span>
              <input
                className="dz-input"
                value={exclude}
                onChange={(event) => { setExclude(event.target.value); setTouched(false) }}
                placeholder="original/*, *.onnx"
              />
            </label>
          </div>
        )}
        <label className="safety-toggle" style={{ display: 'block', marginTop: 14 }}>
          <input
            type="checkbox"
            checked={excludeUnsafe}
            onChange={(event) => setExcludeUnsafe(event.target.checked)}
          />
          <span>
            <strong>Exclude pickle-compatible files</strong>
            <small>Skip .bin, .pt, .pth, .pkl, .pickle, and .ckpt artifacts</small>
          </span>
        </label>

        <div className="download-foot">
          <button type="button" className="download-button" onClick={queue} disabled={queuing || effectiveSet.size === 0}>
            {queuing ? <LoaderCircle size={15} className="spin" /> : <Download size={15} />}
            {queuing ? 'Adding to queue…' : model.local ? 'Pull latest revision' : 'Start download'}
          </button>
          <span className="est">{effectiveFiles.length} files / {formatBytes(estimatedBytes)}</span>
        </div>
        {error && <div className="inline-error">{error}</div>}
      </section>

      <section>
        <div className="file-select-head">
          <span className="field-label">Select files</span>
          <span className="fs-actions">
            <button type="button" className="secondary-button compact" onClick={selectAll}>All</button>
            <button type="button" className="secondary-button compact" onClick={selectNone}>None</button>
          </span>
        </div>
        <div className="file-table">
          <div className="ft-head">
            <span />
            <span>File</span>
            <span style={{ textAlign: 'right' }}>Size</span>
          </div>
          <div className="ft-scroll">
            {model.files.map((file) => (
              <div className="ft-row" key={file.path}>
                <input
                  type="checkbox"
                  checked={effectiveSet.has(file.path)}
                  onChange={(event) => toggleFile(file.path, event.target.checked)}
                  aria-label={file.path}
                />
                <span className="ft-path" title={file.path}>{file.path}</span>
                <span className="ft-size">{file.size ? formatBytes(file.size) : '—'}</span>
              </div>
            ))}
          </div>
          <div className="ft-foot">
            <span>{effectiveSet.size} of {model.files.length} selected</span>
            <strong>{formatBytes(estimatedBytes)}</strong>
          </div>
        </div>
      </section>
    </div>
  )

  function presetEstimate(id: DownloadMode): number {
    if (id === mode) return estimatedBytes
    const files = model.files.filter((file) => {
      if (id === 'full') return true
      if (id === 'safetensors') return file.path.toLowerCase().endsWith('.safetensors') || isMetadataFile(file)
      if (id === 'gguf') return file.path === ggufPath || isMetadataFile(file)
      if (id === 'metadata') return isMetadataFile(file)
      return true
    })
    return files.reduce((total, file) => total + (file.size || 0), 0)
  }
}
