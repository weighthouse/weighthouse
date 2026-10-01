/**
 * Deletion-plan helpers for the Local library.
 *
 * Pure presentation logic over the backend's versioned deletion proposal
 * (backend/app/deletion.py): which rows are deletable, what a selection
 * frees, and the confirmation gate for whole-copy removal. The proposal
 * is authoritative; these helpers never invent rows or sizes.
 */

export interface DeletionRow {
  path: string
  size: number
  kind?: string
  blob?: string | null
}

export interface DeletionPlan {
  schema_version: number
  repo_id: string | null
  tracked: DeletionRow[]
  extras: DeletionRow[]
  whole_copy: { size_bytes: number; file_count: number; tracked_manifest: boolean }
  built_at: string
}

/** Every row an apply request may target, tracked first then leftovers. */
export function deletableRows(plan: DeletionPlan): DeletionRow[] {
  return [...(plan.tracked || []), ...(plan.extras || [])]
}

/** Total bytes the given rows would free (sizes are authoritative). */
export function freedBytesOf(rows: DeletionRow[]): number {
  return rows.reduce((total, row) => total + (row.size || 0), 0)
}

/** Whole-copy deletion confirmation: the repository id, exactly. */
export function copyDeletionConfirmed(plan: DeletionPlan, confirmation: string): boolean {
  return confirmation.trim() === plan.repo_id
}
