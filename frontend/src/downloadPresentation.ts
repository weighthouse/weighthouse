import type { DownloadJob } from './types'

export function sortDownloads(jobs: DownloadJob[]): DownloadJob[] {
  const rank = (job: DownloadJob) => {
    if (job.status === 'downloading' || job.status === 'preparing') return 0
    if (job.status === 'queued') return 1
    if (job.status === 'paused') return 2
    return 3
  }
  return [...jobs].sort((a, b) => {
    const group = rank(a) - rank(b)
    if (group) return group
    if (a.status === 'queued' && b.status === 'queued') {
      const priority = (b.priority || 0) - (a.priority || 0)
      if (priority) return priority
    }
    if (rank(a) === 1 || rank(a) === 2) {
      const queued = (a.queued_at || a.created_at).localeCompare(b.queued_at || b.created_at)
      if (queued) return queued
    }
    const finished = (b.completed_at || b.created_at).localeCompare(a.completed_at || a.created_at)
    return finished || a.id.localeCompare(b.id)
  })
}

export function downloadErrorText(job: DownloadJob): string {
  const output = typeof job.metadata.output_tail === 'string' ? job.metadata.output_tail : ''
  const engine = job.metadata.engine === 'xet' ? 'Xet transport' : job.metadata.engine === 'http' ? 'HTTP transport' : ''
  const log = typeof job.metadata.xet_log === 'string' ? job.metadata.xet_log : ''
  return [job.repo_id, job.error, output !== job.error ? output : '', engine, log].filter(Boolean).join('\n\n')
}

export function downloadErrorSummary(job: DownloadJob): string {
  return job.metadata.worker_returncode === -9 ? 'Process killed (SIGKILL)' : 'Error details'
}
