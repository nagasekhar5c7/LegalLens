import type { Citation, DocStatus, DocType } from '@/api/types'

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "just now" · "14m" · "3h" · "Tuesday" · "12 Mar" — sidebar density. */
export function relativeTime(iso: string, now = Date.now()): string {
  const diff = now - new Date(iso).getTime()
  if (diff < MINUTE) return 'just now'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m`
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h`
  if (diff < 7 * DAY)
    return new Date(iso).toLocaleDateString(undefined, { weekday: 'long' })
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function absoluteTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const mb = bytes / 1024 / 1024
  if (mb < 1) return `${(bytes / 1024).toFixed(0)} KB`
  return `${mb < 10 ? mb.toFixed(1) : mb.toFixed(0)} MB`
}

export function formatUsd(usd: number): string {
  if (usd === 0) return '$0.00'
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function pluralise(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** "MSA § 12.3 · p. 84" — the label under a citation chip. */
export function citationLabel(c: Pick<Citation, 'documentName' | 'page' | 'section'>): string {
  const doc = shortDocName(c.documentName)
  return c.section ? `${doc} ${c.section} · p. ${c.page}` : `${doc} · p. ${c.page}`
}

/** Long legal filenames do not fit anywhere; abbreviate predictably. */
export function shortDocName(name: string, max = 34): string {
  const stem = name.replace(/\.(pdf|docx|txt)$/i, '')
  return stem.length <= max ? stem : `${stem.slice(0, max - 1).trimEnd()}…`
}

export const DOC_TYPE_LABEL: Record<DocType, string> = {
  contract: 'Contract',
  opinion: 'Opinion',
  pleading: 'Pleading',
  other: 'Other',
}

export const DOC_STATUS_LABEL: Record<DocStatus, string> = {
  queued: 'Queued',
  parsing: 'Parsing',
  ocr: 'Reading scan',
  indexing: 'Indexing',
  ready: 'Ready',
  failed: 'Failed',
}

/** FR-7: anything not `ready` is excluded from retrieval. */
export function isRetrievable(status: DocStatus): boolean {
  return status === 'ready'
}

export function statusExclusionReason(status: DocStatus): string | null {
  switch (status) {
    case 'ready':
      return null
    case 'failed':
      return 'Parsing failed — cannot be searched'
    case 'queued':
      return 'Waiting to be processed'
    case 'ocr':
      return 'Still reading the scan'
    default:
      return 'Still being indexed'
  }
}

/** Sidebar buckets. Order is meaningful and fixed. */
export const CONVERSATION_BUCKETS = [
  'Today',
  'Yesterday',
  'Previous 7 days',
  'Earlier',
] as const

export type ConversationBucket = (typeof CONVERSATION_BUCKETS)[number]

export function bucketFor(iso: string, now = Date.now()): ConversationBucket {
  const then = new Date(iso)
  const startOfToday = new Date(now)
  startOfToday.setHours(0, 0, 0, 0)
  const t = then.getTime()
  if (t >= startOfToday.getTime()) return 'Today'
  if (t >= startOfToday.getTime() - DAY) return 'Yesterday'
  if (t >= startOfToday.getTime() - 7 * DAY) return 'Previous 7 days'
  return 'Earlier'
}

/** Collapse a page list into "4, 7–9, 22" for the OCR warning. */
export function formatPageRanges(pages: number[]): string {
  if (pages.length === 0) return ''
  const sorted = [...pages].sort((a, b) => a - b)
  const out: string[] = []
  let start = sorted[0]
  let prev = sorted[0]
  for (const p of sorted.slice(1)) {
    if (p === prev + 1) {
      prev = p
      continue
    }
    out.push(start === prev ? `${start}` : `${start}–${prev}`)
    start = prev = p
  }
  out.push(start === prev ? `${start}` : `${start}–${prev}`)
  return out.join(', ')
}

/**
 * A thread's name, from the question that opened it.
 *
 * Sent with the NCC call so the sidebar never shows "New chat" × 10. The server
 * has the same rule (`conversations.autotitle`) as a backstop for a thread
 * created some other way, and it defers to a title that is already set.
 */
export function titleFromQuestion(text: string): string {
  const cleaned = text.trim().replace(/\s+/g, ' ').replace(/^(please\s+)?/i, '')
  const short = cleaned.length <= 54 ? cleaned : `${cleaned.slice(0, 53).replace(/\s\S*$/, '')}…`
  return short.charAt(0).toUpperCase() + short.slice(1)
}
