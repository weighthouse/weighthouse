import {
  Archive,
  BookMarked,
  Check,
  ChevronDown,
  ChevronRight,
  ClipboardCopy,
  Eye,
  EyeOff,
  FileUp,
  FolderHeart,
  Heart,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  Plus,
  Trash2,
  UploadCloud,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import { api } from '../api'
import { BRAND } from '../brand.ts'
import type {
  AgentKeyCreated,
  AgentKeyEvent,
  AgentKeyRecord,
  AuthStatus,
  Collection,
  Health,
  OwnedRepository,
  SavedModel,
  User,
} from '../types'
import { formatBytes, modelHref, relativeTime, taskLabel } from '../utils'
import { BrandMark } from './BrandMarks'
import { useNavigate } from 'react-router-dom'
import { useScrollMemory } from '../useScrollMemory'

type ToastHandler = (message: string, tone?: 'success' | 'error') => void

export function AuthScreen({
  setup,
  onAuthenticated,
}: {
  setup: boolean
  onAuthenticated: (status: AuthStatus) => void
}) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [warning, setWarning] = useState('')

  useEffect(() => {
    // The posture hint arrives ahead of any failure: secure cookies on
    // plain HTTP show up here instead of as a mysteriously bouncing
    // sign-in.
    api
      .authStatus()
      .then((status) => {
        if (status.transport_warning) setWarning(status.transport_warning)
      })
      .catch(() => undefined)
  }, [setup])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      const status = setup
        ? await api.setup({ username, display_name: displayName, password })
        : await api.login({ username, password })
      onAuthenticated(status)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to continue')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-layout">
      <form className="auth-card" onSubmit={submit}>
        <div>
          <BrandMark className="auth-card-mark" />
          <span className="eyebrow">{setup ? 'One-time setup' : `${BRAND.displayName} account`}</span>
          <h2>{setup ? 'Create the owner account' : 'Sign in'}</h2>
        </div>
        {warning && <div className="inline-error" role="alert">{warning}</div>}
        {setup && (
          <label>
            Display name
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Your name"
              maxLength={80}
              autoComplete="name"
            />
          </label>
        )}
        <label>
          Username
          <input
            value={username}
            onChange={(event) => setUsername(event.target.value.toLowerCase())}
            maxLength={32}
            autoCapitalize="none"
            autoComplete="username"
            required
          />
          {setup && <small>3-32 lowercase letters, numbers, underscores, or hyphens.</small>}
        </label>
        <label>
          Password
          <span className="password-field">
            <input
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={setup ? 8 : 1}
              maxLength={256}
              autoComplete={setup ? 'new-password' : 'current-password'}
              required
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </span>
          {setup && <small>Minimum 8 characters. Stored as a salted scrypt hash.</small>}
        </label>
        {error && <div className="inline-error">{error}</div>}
        <button className="download-button auth-submit" disabled={submitting}>
          {submitting ? <LoaderCircle size={17} className="spin" /> : <LockKeyhole size={17} />}
          {submitting ? 'Working…' : setup ? 'Create owner account' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}

export function SavedPage({ onToast, user }: { onToast: ToastHandler; user: User }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<SavedModel[]>([])
  const [collections, setCollections] = useState<Collection[]>([])
  const [collectionId, setCollectionId] = useState('')
  const [query, setQuery] = useState('')
  const [newCollection, setNewCollection] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [draftNote, setDraftNote] = useState('')
  const [draftCollections, setDraftCollections] = useState<string[]>([])

  useScrollMemory('saved')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [saved, groups] = await Promise.all([
        api.savedModels(query, collectionId),
        api.collections(),
      ])
      setItems(saved.items)
      setCollections(groups.items)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to load saved models', 'error')
    } finally {
      setLoading(false)
    }
  }, [collectionId, onToast, query])

  useEffect(() => {
    const timer = window.setTimeout(load, 200)
    return () => window.clearTimeout(timer)
  }, [load])

  async function createCollection(event: FormEvent) {
    event.preventDefault()
    if (!newCollection.trim()) return
    try {
      await api.createCollection({ name: newCollection.trim() })
      setNewCollection('')
      await load()
      onToast('Collection created.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to create collection', 'error')
    }
  }

  async function removeCollection(collection: Collection) {
    try {
      await api.deleteCollection(collection.id)
      if (collectionId === collection.id) setCollectionId('')
      await load()
      onToast(`${collection.name} was deleted. The saved models stay saved.`)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to delete collection', 'error')
    }
  }

  async function remove(item: SavedModel) {
    try {
      await api.unsaveModel(item.repo_id)
      await load()
      onToast(`${item.repo_id} was removed from your saved library.`)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to remove model', 'error')
    }
  }

  async function saveChanges(item: SavedModel) {
    try {
      await api.saveModel({
        repo_id: item.repo_id,
        note: draftNote,
        collection_ids: draftCollections,
        metadata: item.metadata,
      })
      setEditing(null)
      await load()
      onToast('Saved model updated.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to update model', 'error')
    }
  }

  return (
    <>
      <div className="standard-page saved-page">
        <div className="page-heading">
          <div>
            <h1>Saved models</h1>
          </div>
        </div>
        <div className="library-workspace">
          <aside className="collection-sidebar">
            <button
              className={collectionId === '' ? 'active' : ''}
              onClick={() => setCollectionId('')}
            >
              <BookMarked size={16} /> All saved <span>{collectionId === '' ? items.length : ''}</span>
            </button>
            {collections.map((collection) => (
              <span
                key={collection.id}
                className={`collection-row${collectionId === collection.id ? ' active' : ''}`}
              >
                <button
                  type="button"
                  onClick={() => setCollectionId(collection.id)}
                >
                  <Archive size={15} />
                  <span>{collection.name}</span>
                  <em>{collection.model_count}</em>
                </button>
                <button
                  type="button"
                  className="collection-delete"
                  aria-label={`Delete collection ${collection.name}`}
                  onClick={() => removeCollection(collection)}
                >
                  <X size={13} />
                </button>
              </span>
            ))}
            <form onSubmit={createCollection}>
              <input
                value={newCollection}
                onChange={(event) => setNewCollection(event.target.value)}
                placeholder="New collection"
                maxLength={80}
              />
              <button aria-label="Create collection"><Plus size={15} /></button>
            </form>
          </aside>
          <section className="saved-library">
            <div className="catalog-search">
              <Heart size={17} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search saved models and notes"
              />
            </div>
            {loading ? (
              <div className="busy-line"><LoaderCircle className="spin" size={22} /> Loading your library…</div>
            ) : (
              <div className="saved-grid">
                {items.map((item) => (
                  <article className="saved-card" key={item.id}>
                    <button className="saved-card-open" onClick={() => navigate(modelHref(item.repo_id))}>
                      <span className="saved-card-mark">{item.repo_id.slice(0, 2).toUpperCase()}</span>
                      <span>
                        <small>{item.repo_id.split('/')[0]}</small>
                        <strong>{item.repo_id.split('/').slice(1).join('/')}</strong>
                      </span>
                    </button>
                    <div className="saved-card-tags">
                      {item.metadata.pipeline_tag && <span>{taskLabel(item.metadata.pipeline_tag)}</span>}
                      {item.metadata.library_name && <span>{item.metadata.library_name}</span>}
                      {item.metadata.local && <span>In library</span>}
                    </div>
                    {editing === item.id ? (
                      <div className="saved-editor">
                        <textarea
                          value={draftNote}
                          onChange={(event) => setDraftNote(event.target.value)}
                          placeholder="Why is this model worth keeping?"
                          maxLength={1000}
                        />
                        <div className="collection-picks">
                          {collections.map((collection) => (
                            <label key={collection.id}>
                              <input
                                type="checkbox"
                                checked={draftCollections.includes(collection.id)}
                                onChange={() =>
                                  setDraftCollections((current) =>
                                    current.includes(collection.id)
                                      ? current.filter((id) => id !== collection.id)
                                      : [...current, collection.id],
                                  )
                                }
                              />
                              {collection.name}
                            </label>
                          ))}
                        </div>
                        <div className="saved-card-actions">
                          <button className="download-button compact" onClick={() => saveChanges(item)}>
                            <Check size={14} /> Save
                          </button>
                          <button className="secondary-button compact" onClick={() => setEditing(null)}>
                            <X size={14} /> Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p>{item.note || 'No private note yet.'}</p>
                        <div className="saved-card-footer">
                          <small>Saved {relativeTime(item.created_at)}</small>
                          <div>
                            <button
                              onClick={() => {
                                setEditing(item.id)
                                setDraftNote(item.note)
                                setDraftCollections(item.collections)
                              }}
                            >
                              Organize
                            </button>
                            <button className="danger-text" onClick={() => remove(item)}>Remove</button>
                          </div>
                        </div>
                      </>
                    )}
                  </article>
                ))}
                {items.length === 0 && (
                  <div className="empty-state spacious">
                    <FolderHeart size={34} />
                    <h2>Nothing saved here yet</h2>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  )
}

export function UploadsPage({
  user,
  onToast,
}: {
  user: User
  onToast: ToastHandler
}) {
  const folderInput = useRef<HTMLInputElement>(null)
  const [repositories, setRepositories] = useState<OwnedRepository[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [activeRepo, setActiveRepo] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [visibility, setVisibility] = useState<'private' | 'shared'>('private')
  const [files, setFiles] = useState<File[]>([])
  const [progress, setProgress] = useState<Record<string, number>>({})
  const [uploading, setUploading] = useState(false)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    try {
      const [repos, runtime] = await Promise.all([api.uploadRepositories(), api.health()])
      setRepositories(repos.items)
      setHealth(runtime)
      setActiveRepo((current) => current || repos.items.find((item) => item.owner_id === user.id && item.status === 'uploading')?.repo_id || '')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to load repositories', 'error')
    }
  }, [onToast, user.id])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '')
    folderInput.current?.setAttribute('directory', '')
  }, [])

  const totalBytes = useMemo(() => files.reduce((sum, file) => sum + file.size, 0), [files])
  const uploadedBytes = useMemo(
    () => Object.values(progress).reduce((sum, value) => sum + value, 0),
    [progress],
  )
  const active = repositories.find((item) => item.repo_id === activeRepo)
  const usedPct = health
    ? Math.min(100, (health.storage.used_bytes / Math.max(health.storage.total_bytes, 1)) * 100)
    : 0

  async function createRepository(event: FormEvent) {
    event.preventDefault()
    setCreating(true)
    try {
      const repository = await api.createUploadRepository({ slug, description, visibility })
      setSlug('')
      setDescription('')
      setActiveRepo(repository.repo_id)
      await load()
      onToast(`${repository.repo_id} is ready for files.`)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to create repository', 'error')
    } finally {
      setCreating(false)
    }
  }

  function relativeUploadPath(file: File): string {
    const relative = file.webkitRelativePath || file.name
    const parts = relative.split('/').filter(Boolean)
    return parts.length > 1 ? parts.slice(1).join('/') : parts[0]
  }

  async function uploadFolder() {
    if (!active || active.owner_id !== user.id || files.length === 0) return
    setUploading(true)
    try {
      for (const file of files) {
        const path = relativeUploadPath(file)
        await api.uploadFile(
          active.repo_id,
          path,
          file,
          health?.upload_chunk_bytes || 8 * 1024 * 1024,
          (uploaded) => setProgress((current) => ({ ...current, [path]: uploaded })),
        )
      }
      await api.finalizeUploadRepository(active.repo_id)
      setFiles([])
      setProgress({})
      await load()
      onToast(`${active.repo_id} is indexed and ready in the local library.`)
    } catch (reason) {
      onToast(
        `${reason instanceof Error ? reason.message : 'Upload failed'} Progress is saved; retry to resume.`,
        'error',
      )
    } finally {
      setUploading(false)
    }
  }

  async function toggleVisibility(repository: OwnedRepository) {
    try {
      await api.updateUploadRepository(repository.repo_id, {
        description: repository.description,
        visibility: repository.visibility === 'private' ? 'shared' : 'private',
      })
      await load()
      onToast(`${repository.repo_id} visibility updated.`)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to update repository', 'error')
    }
  }

  async function deleteRepository(repository: OwnedRepository) {
    const confirmation = window.prompt(
      `This permanently deletes the repository files from model storage.\n\nType ${repository.repo_id} to continue:`,
    )
    if (confirmation !== repository.repo_id) return
    try {
      await api.deleteUploadRepository(repository.repo_id, confirmation)
      if (activeRepo === repository.repo_id) setActiveRepo('')
      await load()
      onToast(`${repository.repo_id} and its files were deleted.`)
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to delete repository', 'error')
    }
  }

  return (
    <div className="standard-page uploads-page">
      <div className="page-heading">
        <div>
          <h1>Upload models</h1>
        </div>
      </div>
      <section
        className="transfer-overview upload-storage"
        aria-label="Model storage"
      >
        <div className="storage-bar" aria-hidden="true">
          <span style={{ width: `${usedPct}%` }} />
        </div>
        <div><span>Free</span><strong>{health ? formatBytes(health.storage.free_bytes) : '—'}</strong></div>
        <div><span>Used</span><strong>{health ? formatBytes(health.storage.used_bytes) : '—'}</strong></div>
        <div><span>Total</span><strong>{health ? formatBytes(health.storage.total_bytes) : '—'}</strong></div>
        <div><span>Per-file cap</span><strong>{health ? formatBytes(health.max_upload_size_bytes) : '—'}</strong></div>
      </section>
      <div className="upload-columns">
        <section className="settings-section create-repository">
          <div className="settings-section-title">
            <Plus size={20} />
            <div><h2>New repository</h2></div>
          </div>
          <form onSubmit={createRepository}>
            <label>
              Repository name
              <span className="repo-name-input"><code>{user.username}/</code><input value={slug} onChange={(event) => setSlug(event.target.value)} required /></span>
            </label>
            <label>
              Description
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} />
            </label>
            <label>
              Visibility
              <select value={visibility} onChange={(event) => setVisibility(event.target.value as 'private' | 'shared')}>
                <option value="private">Private — only me</option>
                <option value="shared">Shared — all local accounts</option>
              </select>
            </label>
            <button className="download-button" disabled={creating}>
              {creating ? <LoaderCircle size={16} className="spin" /> : <Plus size={16} />}
              Create repository
            </button>
          </form>
        </section>
        <section className="settings-section upload-drop">
          <div className="settings-section-title">
            <UploadCloud size={20} />
            <div><h2>Add a model folder</h2><p>Select an unfinished repository and a folder.</p></div>
          </div>
          <label>
            Repository
            <select value={activeRepo} onChange={(event) => setActiveRepo(event.target.value)}>
              <option value="">Choose a repository</option>
              {repositories
                .filter((item) => item.owner_id === user.id && item.status === 'uploading')
                .map((item) => <option key={item.id} value={item.repo_id}>{item.repo_id}</option>)}
            </select>
          </label>
          <label className="folder-picker">
            <input
              ref={folderInput}
              type="file"
              multiple
              onChange={(event) => {
                setFiles(Array.from(event.target.files || []))
                setProgress({})
              }}
            />
            <FileUp size={25} />
            <strong>{files.length ? `${files.length} files selected` : 'Choose model folder'}</strong>
            <span>{files.length ? formatBytes(totalBytes) : 'Config, tokenizer, weights, and documentation'}</span>
          </label>
          {uploading && (
            <div className="upload-progress">
              <div><span style={{ width: `${totalBytes ? uploadedBytes * 100 / totalBytes : 0}%` }} /></div>
              <small>{formatBytes(uploadedBytes)} of {formatBytes(totalBytes)}</small>
            </div>
          )}
          <button
            className="download-button"
            disabled={!active || !files.length || uploading}
            onClick={uploadFolder}
          >
            {uploading ? <LoaderCircle size={16} className="spin" /> : <UploadCloud size={16} />}
            {uploading ? 'Uploading…' : 'Upload and index'}
          </button>
        </section>
      </div>
      <section className="repository-section">
        <div className="section-heading">
          <div><h2>Account repositories</h2></div>
          <span>{repositories.length} total</span>
        </div>
        <div className="repository-grid">
          {repositories.map((repository) => (
            <article className="owned-repository" key={repository.id}>
              <div className="repository-icon">
                {repository.visibility === 'private' ? <LockKeyhole size={18} /> : <Users size={18} />}
              </div>
              <div>
                <small>{repository.owner_display_name}</small>
                <h3>{repository.repo_id}</h3>
                <p>{repository.description || 'No description yet.'}</p>
                <div className="repo-stats">
                  <span className={`status-pill ${repository.status === 'ready' ? 'ok' : ''}`}>{repository.status}</span>
                  {repository.size_bytes != null && <span>{formatBytes(repository.size_bytes)}</span>}
                  {repository.file_count != null && <span>{repository.file_count} files</span>}
                </div>
              </div>
              {repository.owner_id === user.id && (
                <div className="repository-actions">
                  <button onClick={() => toggleVisibility(repository)}>
                    {repository.visibility === 'private' ? <Users size={14} /> : <LockKeyhole size={14} />}
                    {repository.visibility === 'private' ? 'Share locally' : 'Make private'}
                  </button>
                  <button className="danger-text" onClick={() => deleteRepository(repository)}>
                    <Trash2 size={14} /> Delete
                  </button>
                </div>
              )}
            </article>
          ))}
          {repositories.length === 0 && <div className="empty-compact">No account-owned repositories yet.</div>}
        </div>
      </section>
    </div>
  )
}

export function AccountAdmin({
  user,
  users,
  onUsersChanged,
  onToast,
}: {
  user: User
  users: User[]
  onUsersChanged?: () => void
  onToast: ToastHandler
}) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [creating, setCreating] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [changingPassword, setChangingPassword] = useState(false)

  async function createMember(event: FormEvent) {
    event.preventDefault()
    setCreating(true)
    try {
      await api.createUser({ username, display_name: displayName, password })
      setUsername('')
      setDisplayName('')
      setPassword('')
      onUsersChanged?.()
      onToast('Member account created.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to create account', 'error')
    } finally {
      setCreating(false)
    }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault()
    setChangingPassword(true)
    try {
      await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setCurrentPassword('')
      setNewPassword('')
      onToast('Password changed. Other sessions were signed out.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to change password', 'error')
    } finally {
      setChangingPassword(false)
    }
  }

  return (
    <div className="settings-section" id="accounts">
      <div className="s-head">
        <h2>Accounts</h2>
      </div>

      <div className="subhead">
        <h3>Members</h3>
      </div>
      {users.map((member) => (
        <div key={member.id} className="member-row">
          <span className="member-avatar" aria-hidden="true">
            {member.display_name.slice(0, 2).toUpperCase()}
          </span>
          <span className="member-ident">
            <span className="m-name">{member.display_name}</span>
            <span className="m-user">@{member.username}</span>
          </span>
          <span className="m-role">{member.role}</span>
        </div>
      ))}

      {user.role === 'admin' && (
        <>
          <div className="subhead">
            <h3>Add a member</h3>
          </div>
          <form className="inline-form" onSubmit={createMember}>
            <div className="field">
              <label className="field-label" htmlFor="member-display">Display name</label>
              <input
                id="member-display"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Mira"
                maxLength={80}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="member-username">Username</label>
              <input
                id="member-username"
                className="mono"
                value={username}
                onChange={(event) => setUsername(event.target.value.toLowerCase())}
                placeholder="mira"
                maxLength={32}
                required
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="member-password">
                Password <small>12+ characters</small>
              </label>
              <input
                id="member-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={12}
                maxLength={256}
                required
              />
            </div>
            <button className="secondary-button" disabled={creating}>
              {creating ? <LoaderCircle size={15} className="spin" /> : <UserPlus size={15} />}
              Create member
            </button>
          </form>
        </>
      )}

      <div className="subhead">
        <h3>Change my password</h3>
      </div>
      <form className="inline-form two-col" onSubmit={changePassword}>
        <div className="field">
          <label className="field-label" htmlFor="pw-current">Current password</label>
          <input
            id="pw-current"
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            maxLength={256}
            required
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="pw-new">
            New password <small>12+ characters</small>
          </label>
          <input
            id="pw-new"
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            minLength={12}
            maxLength={256}
            required
          />
        </div>
        <button className="secondary-button" disabled={changingPassword}>
          {changingPassword ? <LoaderCircle size={15} className="spin" /> : <LockKeyhole size={15} />}
          Update password
        </button>
      </form>
    </div>
  )
}

const AGENT_KEY_DEFAULT_SCOPES = ['search', 'read']

export function AgentKeysAdmin({
  user,
  users,
  onToast,
  onCount,
}: {
  user: User
  users: User[]
  onToast: ToastHandler
  onCount?: (count: number) => void
}) {
  const [keys, setKeys] = useState<AgentKeyRecord[]>([])
  const [ownerId, setOwnerId] = useState(user.id)
  const [scopeCatalog, setScopeCatalog] = useState<Record<string, string>>({})
  const [maxActive, setMaxActive] = useState(0)
  const [name, setName] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set(AGENT_KEY_DEFAULT_SCOPES))
  const [creating, setCreating] = useState(false)
  const [minted, setMinted] = useState<AgentKeyCreated | null>(null)
  const [copied, setCopied] = useState(false)
  const [openEvents, setOpenEvents] = useState<Record<string, AgentKeyEvent[] | 'loading'>>({})
  // Revoked keys stay for the record, but they don't get to crowd the
  // list: they live behind a collapsed count that opens on demand —
  // where each one can be deleted outright (which truly removes it and
  // its request history) or left where it is.
  const [showRevoked, setShowRevoked] = useState(false)

  const load = useCallback(() => {
    if (user.role !== 'admin') return
    api
      .agentKeys()
      .then((payload) => {
        setKeys(payload.items)
        setScopeCatalog(payload.scopes || {})
        setMaxActive(payload.max_active)
      })
      .catch(() => undefined)
  }, [user.role])

  useEffect(() => {
    load()
  }, [load])

  const activeKeys = keys.filter((key) => !key.revoked_at)
  const revokedKeys = keys.filter((key) => key.revoked_at)

  useEffect(() => {
    onCount?.(activeKeys.length)
  }, [activeKeys, onCount])

  function toggleScope(scope: string, checked: boolean) {
    setPicked((current) => {
      const next = new Set(current)
      if (checked) next.add(scope)
      else next.delete(scope)
      return next
    })
  }

  async function createKey(event: FormEvent) {
    event.preventDefault()
    if (picked.size === 0) return
    setCreating(true)
    try {
      const created = await api.createAgentKey({
        name,
        scopes: [...picked],
        user_id: ownerId,
      })
      setMinted(created)
      setCopied(false)
      setName('')
      setPicked(new Set(AGENT_KEY_DEFAULT_SCOPES))
      load()
      onToast('Agent key created. Copy the token now — it is shown once.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to create the agent key.', 'error')
    } finally {
      setCreating(false)
    }
  }

  async function revokeKey(key: AgentKeyRecord) {
    if (!window.confirm(`Revoke the agent key "${key.name}"? Requests using it stop immediately.`)) {
      return
    }
    try {
      await api.revokeAgentKey(key.id)
      onToast(`${key.name} was revoked.`)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to revoke the key.', 'error')
    }
  }

  async function purgeKey(key: AgentKeyRecord) {
    if (!window.confirm(
      `Delete the agent key "${key.name}" permanently? Its record and request history are removed, and this cannot be undone.`,
    )) {
      return
    }
    try {
      await api.deleteAgentKey(key.id)
      onToast(`${key.name} was deleted along with its request history.`)
      load()
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Unable to delete the key.', 'error')
    }
  }

  async function toggleHistory(keyId: string) {
    if (openEvents[keyId]) {
      setOpenEvents((current) => {
        const next = { ...current }
        delete next[keyId]
        return next
      })
      return
    }
    setOpenEvents((current) => ({ ...current, [keyId]: 'loading' }))
    try {
      const payload = await api.agentKeyEvents(keyId)
      setOpenEvents((current) => ({ ...current, [keyId]: payload.items }))
    } catch {
      setOpenEvents((current) => {
        const next = { ...current }
        delete next[keyId]
        return next
      })
      onToast('Could not load the request history.', 'error')
    }
  }

  async function copyMintedToken() {
    if (!minted) return
    try {
      await navigator.clipboard.writeText(minted.key)
      setCopied(true)
    } catch {
      onToast('Copy failed — select the token text manually.', 'error')
    }
  }

  // One row builder for both shelves: active keys carry a Revoke button,
  // revoked ones carry Delete — the end of the record, not a second stamp.
  // A key acts as the account it belongs to, so the owner rides on the
  // name itself.
  function keyBlock(list: AgentKeyRecord[]) {
    return list.map((key) => (
      <div key={key.id} className={key.revoked_at ? 'key-row revoked' : 'key-row'}>
        <span className="k-name">
          <strong>{key.name}</strong>
          <span className="k-user">@{key.user.username || 'unknown'}</span>
        </span>
        <span className="scopes">
          {key.scopes.map((scope) => (
            <span key={scope} className="scope-chip">{scope}</span>
          ))}
        </span>
        <span className="k-meta">
          created {relativeTime(key.created_at)}
          <br />
          {key.revoked_at
            ? `revoked ${relativeTime(key.revoked_at)}`
            : key.last_used_at
              ? `last used ${relativeTime(key.last_used_at)}`
              : 'never used'}
        </span>
        <span className="key-actions">
          <button
            type="button"
            className="secondary-button compact"
            onClick={() => toggleHistory(key.id)}
          >
            {openEvents[key.id] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {openEvents[key.id] ? 'Hide history' : 'History'}
          </button>
          {key.revoked_at ? (
            <button type="button" className="secondary-button compact danger" onClick={() => purgeKey(key)}>
              <Trash2 size={13} /> Delete
            </button>
          ) : (
            <button
              type="button"
              className="secondary-button compact danger"
              onClick={() => revokeKey(key)}
            >
              <X size={13} /> Revoke
            </button>
          )}
        </span>
        {openEvents[key.id] && (
          <div className="key-events">
            {openEvents[key.id] === 'loading' && <div className="empty-compact">Loading history…</div>}
            {Array.isArray(openEvents[key.id]) && (openEvents[key.id] as AgentKeyEvent[]).length === 0 && (
              <div className="empty-compact">No requests through this key yet.</div>
            )}
            {Array.isArray(openEvents[key.id]) && (openEvents[key.id] as AgentKeyEvent[]).map((event) => (
              <div key={event.id} className="key-event-row">
                <span className="key-method">{event.method}</span>
                <span className="key-path" title={event.path}>{event.path}</span>
                <strong className={event.status < 400 ? 'good-text' : 'danger-text'}>
                  {event.status}
                </strong>
                <small>{relativeTime(event.created_at)}</small>
              </div>
            ))}
          </div>
        )}
      </div>
    ))
  }

  if (user.role !== 'admin') return null

  return (
    <div className="settings-section" id="keys">
      <div className="s-head">
        <h2>Agent keys</h2>
        {maxActive > 0 && (
          <span className="s-desc-inline">{activeKeys.length} of {maxActive} active</span>
        )}
      </div>

      <div className="key-rows">
        {keyBlock(activeKeys)}
        {activeKeys.length === 0 && revokedKeys.length === 0 && (
          <div className="empty-compact">No agent keys yet.</div>
        )}
        {activeKeys.length === 0 && revokedKeys.length > 0 && (
          <div className="empty-compact">No active agent keys.</div>
        )}
        {revokedKeys.length > 0 && (
          <div className="key-revoked-shelf">
            <button
              type="button"
              className="key-revoked-toggle"
              onClick={() => setShowRevoked((open) => !open)}
              aria-expanded={showRevoked}
            >
              {showRevoked ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              Revoked keys ({revokedKeys.length})
            </button>
            {showRevoked && keyBlock(revokedKeys)}
          </div>
        )}
      </div>

      <div className="subhead">
        <h3>Create a key</h3>
        <p>A key acts as the account it belongs to.</p>
      </div>
      <form className="inline-form key-form" onSubmit={createKey}>
        <div className="field">
          <label className="field-label" htmlFor="key-name">Name</label>
          <input
            id="key-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. inventory agent"
            maxLength={60}
            required
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="key-owner">User</label>
          <select
            id="key-owner"
            className="mono"
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
          >
            {users.map((member) => (
              <option key={member.id} value={member.id}>
                {member.id === user.id ? `you (${member.display_name})` : `@${member.username}`}
              </option>
            ))}
          </select>
        </div>
        <button className="secondary-button" disabled={creating || picked.size === 0}>
          {creating ? <LoaderCircle size={15} className="spin" /> : <KeyRound size={15} />}
          Mint key
        </button>
      </form>
      <div className="scope-checks">
        {Object.entries(scopeCatalog).map(([scope, description]) => (
          <label key={scope} className="check">
            <input
              type="checkbox"
              checked={picked.has(scope)}
              onChange={(event) => toggleScope(scope, event.target.checked)}
            />
            <span>
              <strong>{scope}</strong>
              <small>{description}</small>
            </span>
          </label>
        ))}
      </div>
      {minted && (
        <>
          <div className="minted-box">
            <code title={minted.key}>{minted.key}</code>
            <button type="button" className="secondary-button compact" onClick={copyMintedToken}>
              <ClipboardCopy size={13} /> Copy token
            </button>
            <button
              type="button"
              className="secondary-button compact"
              onClick={() => {
                setMinted(null)
                setCopied(false)
              }}
            >
              <Check size={13} /> I've stored it
            </button>
          </div>
          {copied && <p className="copied-hint">Token copied!</p>}
        </>
      )}
    </div>
  )
}
