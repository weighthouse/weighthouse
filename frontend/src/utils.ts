export function formatBytes(bytes = 0, precision = 1): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : precision)} ${units[index]}`
}

export function formatNumber(value = 0): string {
  return new Intl.NumberFormat('en', {
    notation: value >= 10_000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(value)
}

export function relativeTime(value?: string | null): string {
  if (!value) return 'Unknown'
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return 'Unknown'
  const seconds = Math.round((timestamp - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['week', 604_800],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ]
  for (const [unit, size] of ranges) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit)
  }
  return formatter.format(seconds, 'second')
}

export function initials(repoId: string): string {
  const parts = repoId.split('/')
  return (parts[0]?.slice(0, 2) || 'HH').toUpperCase()
}

export function taskLabel(task?: string | null): string {
  if (!task) return 'Model'
  return task
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}


// Every door into the model page builds its address here, so the link
// shape lives in exactly one place. Owner and name are percent-encoded
// separately; the slash stays literal as the route separator.
export function modelHref(repoId: string): string {
  const [owner, ...rest] = repoId.split('/')
  const name = rest.join('/')
  if (!owner || !name) return '/models'
  return `/model/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`
}

// Parameter counts render the way people say them: 70B, 12.5B, 809M.
export function formatParameterCount(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return ''
  if (count >= 1_000_000_000) {
    const billions = count / 1_000_000_000
    return `${billions >= 10 ? Math.round(billions) : Math.round(billions * 10) / 10}B`
  }
  if (count >= 1_000_000) return `${Math.round(count / 1_000_000)}M`
  return new Intl.NumberFormat().format(count)
}
