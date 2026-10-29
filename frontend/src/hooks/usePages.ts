import { useCallback, useEffect, useRef, useState } from 'react'
import type { Page, Span } from '@/api/types'
import { api } from '@/api/client'
import { isAbort } from '@/api/http'

/**
 * Page text for the viewer, fetched on demand and cached.
 *
 * A 500-page document is the one real engineering risk in the viewer, and it
 * cuts both ways now that pages come from the network: the window is only ±2
 * pages, but scrolling from page 1 to page 312 passes over three hundred of
 * them. So every request carries an `AbortController` keyed by page, and
 * anything that leaves the window while still in flight is cancelled rather
 * than left to resolve into a cache nobody will read.
 *
 * `GET /api/documents/{id}/pages/{n}` sends an `ETag` and
 * `Cache-Control: private, max-age=300`, so a page revisited inside the window
 * is a 304 or a memory hit and never a re-read of encrypted text.
 */
export interface PageCache {
  /** `undefined` while loading — `PageSurface` renders its skeleton for null. */
  get: (page: number) => Page | null
  /** Set for a page whose fetch failed, so the surface can say so. */
  errorAt: (page: number) => string | null
}

export function usePages(documentId: string, pages: number[]): PageCache {
  const [cache, setCache] = useState<Map<number, Page>>(new Map())
  const [errors, setErrors] = useState<Map<number, string>>(new Map())
  const pending = useRef(new Map<number, AbortController>())

  // A new document invalidates everything, including anything in flight.
  useEffect(() => {
    for (const controller of pending.current.values()) controller.abort()
    pending.current.clear()
    setCache(new Map())
    setErrors(new Map())
  }, [documentId])

  // `pages` is a fresh array every render, so the effect keys off its content.
  const key = pages.join(',')

  useEffect(() => {
    const wanted = new Set(pages)

    // Cancel what has scrolled out of the window: on a fast scroll this is the
    // difference between five requests and three hundred.
    for (const [page, controller] of pending.current) {
      if (!wanted.has(page)) {
        controller.abort()
        pending.current.delete(page)
      }
    }

    for (const page of pages) {
      if (cache.has(page) || errors.has(page) || pending.current.has(page)) continue
      const controller = new AbortController()
      pending.current.set(page, controller)
      void api
        .getPage(documentId, page, controller.signal)
        .then((loaded) => {
          setCache((prev) => new Map(prev).set(page, loaded))
        })
        .catch((thrown) => {
          if (isAbort(thrown)) return
          // A page the server has no record of is normal while a document is
          // still indexing, so this is stated rather than thrown.
          setErrors((prev) =>
            new Map(prev).set(page, 'This page has not been extracted yet.'),
          )
        })
        .finally(() => {
          pending.current.delete(page)
        })
    }
    // `cache` and `errors` are read, not depended on: including them would
    // re-run this on every arrival and re-check pages already resolved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, key])

  // Abort everything still open when the viewer unmounts.
  useEffect(
    () => () => {
      for (const controller of pending.current.values()) controller.abort()
      pending.current.clear()
    },
    [],
  )

  const get = useCallback((page: number) => cache.get(page) ?? null, [cache])
  const errorAt = useCallback((page: number) => errors.get(page) ?? null, [errors])

  return { get, errorAt }
}

/**
 * One span, resolved to its page and character range (FR-11).
 *
 * `null` covers both "no span asked for" and "that span is gone" — a citation
 * whose document has since been deleted should not break the viewer.
 */
export function useSpan(spanId: string | null | undefined) {
  const [span, setSpan] = useState<Span | null>(null)

  useEffect(() => {
    if (!spanId) {
      setSpan(null)
      return
    }
    let live = true
    void api
      .getSpan(spanId)
      .then((resolved) => {
        if (live) setSpan(resolved)
      })
      .catch(() => {
        if (live) setSpan(null)
      })
    return () => {
      live = false
    }
  }, [spanId])

  return span
}
