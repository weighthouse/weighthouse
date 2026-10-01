import { useEffect } from 'react'
import { currentScroll, rememberScroll, restoreScroll } from './scrollMemory'

// One list page = one hook: the key is the route identity, saving rides
// a passive listener (cheap, fires only while the page is visible and
// the user is actually scrolling), restore runs on mount. Unmounting a
// list always records its final position, so a model visit mid-scroll
// comes back to the same spot whether the user left by card click,
// browser back proceeds, or a tab was restored.
export function useScrollMemory(routeKey: string): void {
  useEffect(() => {
    const save = () => rememberScroll(routeKey, currentScroll())
    window.addEventListener('scroll', save, { passive: true })
    restoreScroll(routeKey)
    return () => {
      window.removeEventListener('scroll', save)
      save()
    }
  }, [routeKey])
}
