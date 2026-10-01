import { BRAND } from './brand.ts'
import type {
  AgentKeyCreated,
  AgentKeyEvent,
  AgentKeyList,
  AuthStatus,
  Collection,
  DownloadJob,
  DownloadMode,
  Health,
  HubModel,
  HubModelDetails,
  LibraryFacets,
  LocalModel,
  LocalModelDetails,
  ModelNote,
  LocalCardSnapshot,
  MoveResult,
  OwnedRepository,
  RuntimeJob,
  RuntimeTarget,
  SavedModel,
  StorageLocationInfo,
  User,
  VerifyReport,
  WatchList,
  WatchRecord,
} from './types'
import type { UpdateDiff } from './updateDiff.ts'
import type { DeletionPlan } from './deletion.ts'

let csrfToken: string | null = null

function applyAuth(status: AuthStatus): AuthStatus {
  csrfToken = status.csrf_token
  return status
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (csrfToken && init?.method && !['GET', 'HEAD'].includes(init.method)) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers,
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event(BRAND.unauthorizedEvent))
    throw new Error(payload.detail || `Request failed with status ${response.status}`)
  }
  return payload as T
}

const repoPath = (repoId: string) =>
  repoId
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')

export const api = {
  authStatus: () => request<AuthStatus>('/api/auth/status').then(applyAuth),
  setup: (payload: { username: string; display_name: string; password: string }) =>
    request<AuthStatus>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(applyAuth),
  login: (payload: { username: string; password: string }) =>
    request<AuthStatus>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
    }).then(applyAuth),
  logout: () =>
    request<{ status: string }>('/api/auth/logout', { method: 'POST' }).finally(() => {
      csrfToken = null
    }),
  users: () => request<{ items: User[] }>('/api/users'),
  createUser: (payload: { username: string; display_name: string; password: string }) =>
    request<User>('/api/users', { method: 'POST', body: JSON.stringify(payload) }),
  agentKeys: () => request<AgentKeyList>('/api/agent-keys'),
  createAgentKey: (payload: { name: string; scopes: string[]; user_id?: string }) =>
    request<AgentKeyCreated>('/api/agent-keys', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  revokeAgentKey: (keyId: string) =>
    request<{ status: string }>(`/api/agent-keys/${encodeURIComponent(keyId)}`, {
      method: 'DELETE',
    }),
  deleteAgentKey: (keyId: string) =>
    request<{ status: string }>(`/api/agent-keys/${encodeURIComponent(keyId)}/purge`, {
      method: 'DELETE',
    }),
  agentKeyEvents: (keyId: string) =>
    request<{ items: AgentKeyEvent[] }>(
      `/api/agent-keys/${encodeURIComponent(keyId)}/events`,
    ),
  watches: () => request<WatchList>('/api/watches'),
  checkWatchNow: () =>
    request<{ checked: number; unreachable: number }>('/api/watches/check', { method: 'POST' }),
  watchModel: (repoId: string) =>
    request<WatchRecord>('/api/watches', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repoId }),
    }),
  unwatchModel: (repoId: string) =>
    request<{ status: string }>(`/api/watches/${repoPath(repoId)}`, {
      method: 'DELETE',
    }),
  markWatchSeen: (repoId: string) =>
    request<WatchRecord>(`/api/watches/${repoPath(repoId)}/seen`, {
      method: 'POST',
    }),
  changePassword: (payload: { current_password: string; new_password: string }) =>
    request<{ status: string }>('/api/account/password', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  health: () => request<Health>('/api/health'),
  searchModels: (params: URLSearchParams) =>
    request<{ items: HubModel[]; count: number }>(`/api/hub/models?${params.toString()}`),
  modelDetails: (repoId: string) =>
    request<HubModelDetails>(`/api/hub/models/${repoPath(repoId)}`),
  downloads: () => request<{ items: DownloadJob[]; active: number }>('/api/downloads'),
  startDownload: (payload: {
    repo_id: string
    revision?: string
    mode?: DownloadMode
    gguf_path?: string
    exclude_unsafe?: boolean
    include_patterns?: string[]
    exclude_patterns?: string[]
    watch?: boolean
  }) =>
    request<DownloadJob>('/api/downloads', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  cancelDownload: (downloadId: string) =>
    request<DownloadJob>(`/api/downloads/${encodeURIComponent(downloadId)}/cancel`, {
      method: 'POST',
    }),
  localModels: (query = '', params: URLSearchParams = new URLSearchParams()) => {
    if (query) params.set('query', query)
    return request<{
      items: LocalModel[]
      count: number
      total_bytes: number
      facets?: LibraryFacets
    }>(`/api/local-models?${params.toString()}`)
  },
  preferences: () =>
    request<{ preferences: Record<string, unknown> }>('/api/preferences'),
  savePreferences: (preferences: Record<string, unknown>) =>
    request<{ preferences: Record<string, unknown> }>('/api/preferences', {
      method: 'PUT',
      body: JSON.stringify({ preferences }),
    }),
  modelNotes: (repoId: string) =>
    request<{ items: ModelNote[]; count: number }>(
      `/api/local-models/${repoPath(repoId)}/notes`,
    ),
  localModelCard: (repoId: string) =>
    request<LocalCardSnapshot>(`/api/local-models/${repoPath(repoId)}/card`),
  createModelNote: (repoId: string, body: string) =>
    request<ModelNote>(`/api/local-models/${repoPath(repoId)}/notes`, {
      method: 'POST',
      body: JSON.stringify({ body }),
    }),
  editModelNote: (repoId: string, noteId: string, body: string) =>
    request<ModelNote>(`/api/local-models/${repoPath(repoId)}/notes/${encodeURIComponent(noteId)}`, {
      method: 'PATCH',
      body: JSON.stringify({ body }),
    }),
  pinModelNote: (repoId: string, noteId: string, pinned: boolean) =>
    request<ModelNote>(`/api/local-models/${repoPath(repoId)}/notes/${encodeURIComponent(noteId)}/pin`, {
      method: 'POST',
      body: JSON.stringify({ pinned }),
    }),
  shareModelNotes: (repoId: string, noteId: string, shared: boolean) =>
    request<{ items: ModelNote[] }>(
      `/api/local-models/${repoPath(repoId)}/notes/${encodeURIComponent(noteId)}/share`,
      { method: 'POST', body: JSON.stringify({ shared }) },
    ),
  deleteModelNote: (repoId: string, noteId: string) =>
    request<{ status: string }>(
      `/api/local-models/${repoPath(repoId)}/notes/${encodeURIComponent(noteId)}`,
      { method: 'DELETE' },
    ),
  scanLocalModels: () =>
    request<{ count: number; models: LocalModel[]; scanned_at: string }>(
      '/api/local-models/scan',
      { method: 'POST' },
    ),
  localModelDetails: (repoId: string) =>
    request<LocalModelDetails>(`/api/local-models/${repoPath(repoId)}`),
  localModelUpdates: (repoId: string) =>
    request<UpdateDiff>(`/api/local-models/${repoPath(repoId)}/updates`),
  applyLocalModelUpdates: (repoId: string, paths: string[]) =>
    request<DownloadJob>(`/api/local-models/${repoPath(repoId)}/updates/apply`, {
      method: 'POST',
      body: JSON.stringify({ paths }),
    }),
  localModelDeletionPlan: (repoId: string) =>
    request<DeletionPlan>(`/api/local-models/${repoPath(repoId)}/deletion`),
  deleteLocalModelFiles: (repoId: string, paths: string[]) =>
    request<{ deleted: string[]; freed_bytes: number }>(
      `/api/local-models/${repoPath(repoId)}/deletion/files`,
      { method: 'POST', body: JSON.stringify({ paths }) },
    ),
  deleteLocalModel: (repoId: string, confirmation: string) =>
    request<{ status: string }>(`/api/local-models/${repoPath(repoId)}`, {
      method: 'DELETE',
      body: JSON.stringify({ confirmation }),
    }),
  restoreLocalModel: (repoId: string) =>
    request<LocalModelDetails>(`/api/local-models/${repoPath(repoId)}/restore`, {
      method: 'POST',
    }),
  evictLocalModelCache: (repoId: string) =>
    request<{ status: string; model: LocalModel }>(
      `/api/local-models/${repoPath(repoId)}/cache`,
      { method: 'DELETE' },
    ),
  locations: () =>
    request<{ items: StorageLocationInfo[]; primary: string; count: number }>('/api/locations'),
  moveLocalModel: (repoId: string, destination: string) =>
    request<MoveResult>(`/api/local-models/${repoPath(repoId)}/moves`, {
      method: 'POST',
      body: JSON.stringify({ destination }),
    }),
  verifyLocalModel: (repoId: string) =>
    request<VerifyReport>(`/api/local-models/${repoPath(repoId)}/verify`),
  runtimeTargets: () => request<{ items: RuntimeTarget[] }>('/api/runtimes'),
  runtimeJobs: (limit = 100) =>
    request<{ items: RuntimeJob[]; active: number }>(
      `/api/runtime-jobs?limit=${encodeURIComponent(limit)}`,
    ),
  runtimeJob: (jobId: string) =>
    request<RuntimeJob>(`/api/runtime-jobs/${encodeURIComponent(jobId)}`),
  loadRuntime: (
    targetId: string,
    payload: {
      repo_id: string
      runtime_model_name?: string
      source_file?: string
    },
  ) =>
    request<RuntimeJob>(`/api/runtimes/${encodeURIComponent(targetId)}/load`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  collections: () => request<{ items: Collection[] }>('/api/collections'),
  createCollection: (payload: { name: string; description?: string }) =>
    request<Collection>('/api/collections', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteCollection: (collectionId: string) =>
    request<{ status: string }>(`/api/collections/${encodeURIComponent(collectionId)}`, {
      method: 'DELETE',
    }),
  savedModels: (query = '', collectionId = '') => {
    const params = new URLSearchParams()
    if (query) params.set('query', query)
    if (collectionId) params.set('collection_id', collectionId)
    return request<{ items: SavedModel[]; count: number }>(
      `/api/saved-models?${params.toString()}`,
    )
  },
  saveModel: (payload: {
    repo_id: string
    note?: string
    collection_ids?: string[]
    metadata?: Record<string, unknown>
  }) =>
    request<SavedModel>('/api/saved-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  unsaveModel: (repoId: string) =>
    request<{ status: string }>(`/api/saved-models/${repoPath(repoId)}`, {
      method: 'DELETE',
    }),
  uploadRepositories: () =>
    request<{ items: OwnedRepository[] }>('/api/uploads/repositories'),
  createUploadRepository: (payload: {
    slug: string
    description?: string
    visibility?: 'private' | 'shared'
  }) =>
    request<OwnedRepository>('/api/uploads/repositories', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateUploadRepository: (
    repoId: string,
    payload: { description: string; visibility: 'private' | 'shared' },
  ) =>
    request<OwnedRepository>(
      `/api/uploads/repositories?repo_id=${encodeURIComponent(repoId)}`,
      { method: 'PATCH', body: JSON.stringify(payload) },
    ),
  finalizeUploadRepository: (repoId: string) =>
    request<OwnedRepository>(
      `/api/uploads/repositories/finalize?repo_id=${encodeURIComponent(repoId)}`,
      { method: 'POST' },
    ),
  deleteUploadRepository: (repoId: string, confirmation: string) =>
    request<{ status: string }>(
      `/api/uploads/repositories?repo_id=${encodeURIComponent(repoId)}`,
      {
        method: 'DELETE',
        body: JSON.stringify({ confirmation }),
      },
    ),
  uploadFile: async (
    repoId: string,
    filePath: string,
    file: File,
    chunkBytes: number,
    onProgress: (uploaded: number) => void,
  ) => {
    const params = new URLSearchParams({ repo_id: repoId, path: filePath })
    const status = await request<{ offset: number; complete: boolean }>(
      `/api/uploads/repositories/files/status?${params.toString()}`,
    )
    if (status.complete && status.offset === file.size) {
      onProgress(file.size)
      return
    }
    if (status.complete || status.offset > file.size) {
      throw new Error(`A different completed or partial file already exists at ${filePath}.`)
    }
    let offset = status.offset
    do {
      const chunk = file.slice(offset, Math.min(file.size, offset + chunkBytes))
      const headers = new Headers({
        'Content-Type': 'application/octet-stream',
        'Upload-Offset': String(offset),
        'Upload-Length': String(file.size),
      })
      if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
      const response = await fetch(
        `/api/uploads/repositories/files?${params.toString()}`,
        {
          method: 'PUT',
          credentials: 'same-origin',
          headers,
          body: chunk,
        },
      )
      const result = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(result.detail || `Upload failed with status ${response.status}`)
      }
      offset = result.offset
      onProgress(offset)
      if (file.size === 0) break
    } while (offset < file.size)
  },
}
