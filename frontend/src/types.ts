export interface StorageHealth {
  path: string
  total_bytes: number
  used_bytes: number
  free_bytes: number
  writable: boolean
}

export interface ObjectStorageHealth {
  backend: 'filesystem' | 's3'
  enabled: boolean
  connected: boolean
  bucket?: string | null
  prefix?: string | null
  endpoint?: string | null
  error?: string | null
}

export interface Health {
  status: string
  app: string
  version: string
  database_backend: 'sqlite' | 'postgresql'
  storage: StorageHealth
  object_storage: ObjectStorageHealth
  hf_token_configured: boolean
  hf_endpoint: string
  accounts_enabled: boolean
  upload_chunk_bytes: number
  max_upload_size_bytes: number
  runtime_target_count: number
  runtime_api_token_configured: boolean
}

export interface User {
  id: string
  username: string
  display_name: string
  role: 'admin' | 'member'
  created_at: string
}

export interface AgentKeyRecord {
  id: string
  name: string
  key_hint: string
  scopes: string[]
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
  user: {
    id: string | null
    username: string | null
    display_name: string | null
  }
}

export interface AgentKeyCreated {
  key: string
  record: AgentKeyRecord
}

export interface AgentKeyList {
  items: AgentKeyRecord[]
  scopes: Record<string, string>
  max_active: number
}

export interface AgentKeyEvent {
  id: string
  key_id: string
  method: string
  path: string
  status: number
  created_at: string
}

export interface WatchRecord {
  repo_id: string
  watched_since: string
  checked_at: string | null
  change_detected_at: string | null
  has_copy: boolean
  update_available: boolean
  state: 'copy' | 'no-copy'
  copy_sha: string | null
  copy: {
    size_bytes: number | null
    location: string | null
    storage_backend: string
    cached: boolean
    parameter_count: number | null
  } | null
  upstream_sha: string | null
  last_seen_sha: string | null
  note_preview?: NotePreview | null
  metadata: {
    author?: string | null
    pipeline_tag?: string | null
    library_name?: string | null
    license?: string | null
  }
}

export interface WatchList {
  items: WatchRecord[]
  count: number
  stale_count: number
}

export interface AuthStatus {
  accounts_enabled: boolean
  setup_required: boolean
  user: User | null
  csrf_token: string | null
  transport_warning?: string | null
}

export interface HubFile {
  path: string
  size: number
  blob_id?: string | null
}

export interface HubModel {
  id: string
  author?: string | null
  pipeline_tag?: string | null
  library_name?: string | null
  tags: string[]
  downloads: number
  downloads_all_time: number
  likes: number
  trending_score: number
  last_modified?: string | null
  created_at?: string | null
  private: boolean
  gated: boolean | string
  sha?: string | null
  license?: string | null
  parameter_count?: number | null
  quant_count?: number | null
  local?: boolean
  saved?: boolean
}

export interface HubModelDetails extends HubModel {
  revision: string
  files: HubFile[]
  total_bytes: number
  security_status?: unknown
  source_url: string
  model_card?: string | null
}

export type DownloadMode = 'full' | 'safetensors' | 'gguf' | 'metadata' | 'custom'

export interface DownloadJob {
  id: string
  repo_id: string
  revision: string
  status: 'queued' | 'preparing' | 'downloading' | 'paused' | 'complete' | 'failed' | 'cancelled'
  total_bytes: number
  downloaded_bytes: number
  progress: number
  speed_bps: number
  error?: string | null
  target_path?: string | null
  payload: {
    allow_patterns?: string[]
    ignore_patterns?: string[]
    mode?: DownloadMode
    update_merge?: boolean
  }
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
  completed_at?: string | null
}

export interface LocalModel {
  repo_id: string
  relative_path: string
  size_bytes: number
  file_count: number
  modified_at: string
  downloaded_at?: string | null
  revision?: string | null
  sha?: string | null
  pipeline_tag?: string | null
  library_name?: string | null
  license?: string | null
  parameter_count?: number | null
  tags: string[]
  config: Record<string, unknown>
  source_url?: string | null
  managed: boolean
  storage_backend: 'filesystem' | 's3'
  location?: string | null
  cached: boolean
  remote_uri?: string | null
  note_hits?: NoteHit[]
  note_preview?: NotePreview | null
}

export interface ModelNote {
  id: string
  repo_id: string
  author_id: string
  author_name?: string | null
  author_username?: string | null
  body: string
  pinned: boolean
  shared: boolean
  created_at: string
  updated_at: string
}

export interface NoteHit {
  repo_id: string
  body: string
  pinned: boolean
  created_at: string
  author_name?: string | null
}

export interface NotePreview {
  repo_id: string
  author_id: string
  body: string
  pinned: boolean
  created_at: string
  author_name?: string | null
}

export interface FacetBucket {
  value: string
  count: number
}

export interface LibraryFacets {
  total: number
  favorited: number
  watching: number
  tasks: FacetBucket[]
  libraries: FacetBucket[]
  locations: FacetBucket[]
  storage: FacetBucket[]
}

export interface LocalFile {
  path: string
  size: number
  modified_at: string
  unsafe_serialization: boolean
}

export interface LocalModelDetails {
  model: LocalModel
  files: LocalFile[]
  unsafe_file_count: number
  truncated: boolean
}

// The card is pinned to the copy's on-disk sha (D17): card text may be
// absent (no pinned revision, or the upstream could not be read) but is
// then honestly absent — never a stale revision.
export interface LocalCardSnapshot {
  sha: string | null
  card: string | null
  fetched_at: string | null
}

export interface StorageLocationInfo {
  name: string
  root: string
}

export interface MoveResult {
  status: string
  moved_to: string
  path: string
}

export interface VerifyReport {
  schema_version: number
  verified_at: string
  location: string | null
  summary: { present: number; missing: number; 'size-differs': number }
  drifted: boolean
  extras_beyond_manifest: Array<{ path: string; size: number }>
  disk: { size_bytes: number; file_count: number }
  files: Array<{
    path: string
    status: 'present' | 'missing' | 'size-differs'
    expected_size: number
    actual_size: number
    blob: string | null
  }>
  repair: string | null
}

export interface RuntimeTarget {
  id: string
  name: string
  kind: 'ollama' | 'vllm'
  base_url: string
  remote_model_root?: string | null
  authenticated: boolean
  transfer_mode: 'blob-upload' | 'shared-path'
  keep_alive?: string | number | null
}

export interface RuntimeJob {
  id: string
  target_id: string
  target_name: string
  target_kind: 'ollama' | 'vllm'
  repo_id: string
  runtime_model_name: string
  source_file?: string | null
  status: 'queued' | 'preparing' | 'transferring' | 'loading' | 'ready' | 'failed'
  total_bytes: number
  processed_bytes: number
  progress: number
  message: string
  error?: string | null
  created_at: string
  updated_at: string
  completed_at?: string | null
}

export interface Collection {
  id: string
  user_id: string
  name: string
  description: string
  model_count: number
  created_at: string
  updated_at: string
}

export interface SavedModel {
  id: string
  repo_id: string
  note: string
  metadata: {
    author?: string | null
    pipeline_tag?: string | null
    library_name?: string | null
    license?: string | null
    parameter_count?: number | null
    last_modified?: string | null
    local?: boolean
  }
  collections: string[]
  created_at: string
  updated_at: string
}

export interface OwnedRepository {
  id: string
  owner_id: string
  owner_username: string
  owner_display_name: string
  repo_id: string
  description: string
  visibility: 'private' | 'shared'
  status: 'uploading' | 'ready'
  size_bytes?: number | null
  file_count?: number | null
  modified_at?: string | null
  created_at: string
  updated_at: string
}
