import type { WatchRecord } from './types'

export const WATCH_SORTS = ['name', 'kind', 'size', 'params', 'location', 'state', 'note', 'checked'] as const
export type WatchSort = (typeof WATCH_SORTS)[number]

export function watchLocation(copy: WatchRecord['copy']): string {
  if (!copy) return ''
  if (copy.storage_backend === 's3') return copy.cached ? 'S3 cached' : 'S3 only'
  return copy.location && copy.location !== 'primary' ? copy.location : 'local'
}

function valueOf(row: WatchRecord, sort: WatchSort): string | number | null {
  switch (sort) {
    case 'name': return row.repo_id.toLowerCase()
    case 'kind': return row.metadata?.pipeline_tag?.toLowerCase() || null
    case 'size': return row.copy?.size_bytes ?? null
    case 'params': return row.copy?.parameter_count ?? null
    case 'location': return watchLocation(row.copy).toLowerCase() || null
    case 'state': return row.update_available ? (row.has_copy ? 2 : 1) : 0
    case 'note': return row.note_preview?.body.toLowerCase() || null
    case 'checked': return row.checked_at || null
  }
}

export function sortWatchRows(rows: WatchRecord[], sort: WatchSort, order: 'asc' | 'desc'): WatchRecord[] {
  const factor = order === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const av = valueOf(a, sort)
    const bv = valueOf(b, sort)
    if (av === null || bv === null) return av === bv ? a.repo_id.localeCompare(b.repo_id) : av === null ? 1 : -1
    const compared = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv))
    if (compared) return factor * compared
    if (sort === 'state') {
      const changed = (b.change_detected_at || b.checked_at || '').localeCompare(a.change_detected_at || a.checked_at || '')
      if (changed) return changed
    }
    return a.repo_id.localeCompare(b.repo_id)
  })
}
