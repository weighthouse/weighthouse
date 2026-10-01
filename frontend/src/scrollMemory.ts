// List scroll memory: a route change to a model page and the browser's
// back button are the same history entries, so the lists the app keeps
// remember where their viewport was. Each list page saves its scroll
// offset under its own key; returning to the route restores it. Content
// loads after mount on these pages, so restore is retried across a
// couple of frames until the document is tall enough to honor it.

const PREFIX = 'mh-scroll:'

export function currentScroll(): number {
  return window.scrollY
}

function keyFor(routeKey: string): string {
  return PREFIX + routeKey
}

export function rememberScroll(routeKey: string, offset: number): void {
  try {
    sessionStorage.setItem(keyFor(routeKey), String(Math.max(0, Math.round(offset))))
  } catch {
    // Storage may be unavailable (private mode, quota); memory is a
    // convenience here, never a correctness requirement.
  }
}

export function peekScroll(routeKey: string): number | null {
  try {
    const stored = sessionStorage.getItem(keyFor(routeKey))
    return stored === null ? null : Number(stored)
  } catch {
    return null
  }
}

export function forgetScroll(routeKey: string): void {
  try {
    sessionStorage.removeItem(keyFor(routeKey))
  } catch {
    // Best effort, like the rest of this module.
  }
}

// Restore tries once, then keeps retrying on animation frames while the
// document is still too short to reach the remembered offset — enough
// frames for async list data to paint (Explore debounces its search by
// 350ms before the first fetch even fires). If the content shrank for
// good, the entry is dropped.
export function restoreScroll(routeKey: string, attempts = 96): void {
  const target = peekScroll(routeKey)
  if (target === null || target <= 0) return
  let remaining = Math.max(1, attempts)
  const tryOnce = () => {
    window.scrollTo(0, target)
    remaining -= 1
    if (Math.abs(window.scrollY - target) <= 2 || remaining <= 0) {
      if (Math.abs(window.scrollY - target) > 2) forgetScroll(routeKey)
      return
    }
    window.requestAnimationFrame(tryOnce)
  }
  window.requestAnimationFrame(tryOnce)
}
