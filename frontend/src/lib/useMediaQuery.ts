import { useEffect, useState } from 'react'

/** Layout breakpoints, named so the shell reads as intent not numbers. */
export const BREAKPOINT = {
  /** Three panes side by side. */
  threePane: '(min-width: 1280px)',
  /** Sidebar in-grid, evidence as an overlay. */
  twoPane: '(min-width: 1024px)',
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches)
    setMatches(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}
