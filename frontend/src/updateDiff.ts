/**
 * Update-diff helpers for the Local library.
 *
 * Pure presentation logic derived from the backend's versioned update
 * artifact (backend/app/updates.py): which files an apply request may
 * target, which upstream removals are report-only, and the summary
 * lines rendered on the model page. The artifact itself is authoritative;
 * these helpers never guess statuses.
 */

export type UpdateFileStatus = 'changed' | 'added' | 'missing' | 'removed' | 'unchanged'

export interface UpdateFileRow {
  path: string
  status: UpdateFileStatus
  local: { size: number; blob: string | null }
  upstream: { size: number; blob: string | null }
  size_delta: number
}

export interface UpdateDiff {
  schema_version: number
  repo_id: string | null
  requested_revision: string
  local: { revision: string | null; sha: string | null; status: string | null }
  upstream: { sha: string; fetched_at: string }
  updates_available: boolean
  results_in: { transfer_bytes: number; changed: number; added: number; missing: number }
  summary: Record<UpdateFileStatus, number>
  added_reporting_limited: boolean
  files: UpdateFileRow[]
}

export type UpdateOutcome = 'queuing' | 'error'

export const UPDATE_STATUS_LABELS: Record<UpdateFileStatus, string> = {
  changed: 'Changed upstream',
  added: 'New upstream',
  missing: 'Needs re-fetch',
  removed: 'Removed upstream',
  unchanged: 'Unchanged',
}

/** Rows an apply request may target (changed, added, missing), in artifact order. */
export function applyableUpdateRows(diff: UpdateDiff): UpdateFileRow[] {
  return (diff.files || []).filter(
    (row) => row.status === 'changed' || row.status === 'added' || row.status === 'missing',
  )
}

/** Rows that are report-only today: upstream removals applied nowhere. */
export function reportOnlyUpdateRows(diff: UpdateDiff): UpdateFileRow[] {
  return (diff.files || []).filter((row) => row.status === 'removed')
}

/** Total upstream bytes of the given rows; 0 for empty/invalid input. */
export function transferBytesOf(rows: UpdateFileRow[]): number {
  return rows.reduce((total, row) => total + (row.upstream?.size || 0), 0)
}

/** Human line like "2 changed · 1 new · 1 removed" (omits zero groups). */
export function updateSummaryLine(diff: UpdateDiff): string {
  const parts: string[] = []
  const counts: Array<[string, number]> = [
    ['changed', diff.summary?.changed ?? 0],
    ['new', diff.summary?.added ?? 0],
    ['to re-fetch', diff.summary?.missing ?? 0],
    ['removed', diff.summary?.removed ?? 0],
  ]
  for (const [label, count] of counts) {
    if (count > 0) parts.push(`${count} ${label}`)
  }
  return parts.join(' · ')
}
