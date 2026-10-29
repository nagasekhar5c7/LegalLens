import { Fragment, type ReactNode } from 'react'
import type { Citation, Excerpt } from '@/api/types'
import { cn } from '@/lib/cn'
import { maskStreamingMarkdown } from '@/lib/markdown'
import { CitationChip } from './CitationChip'

/**
 * A small, streaming-tolerant markdown renderer.
 *
 * Written rather than imported for two reasons: `[[n]]` citation markers have
 * to become interactive React elements inline (a string-replace cannot), and
 * the text arrives a few words at a time, so the parser must render partial
 * syntax without throwing. Element styling lives here as utilities — the
 * element and its appearance in one place.
 */

interface Ctx {
  citations: Citation[]
  excerpts: Excerpt[]
}

const INLINE = /\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|`([^`]+)`|\[\[(\d+)\]\]|\[([^\]]+)\]\(([^)]+)\)/g

function parseInline(text: string, ctx: Ctx, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  let i = 0

  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0
    if (at > last) out.push(text.slice(last, at))
    const key = `${keyPrefix}-${i++}`

    if (m[1] !== undefined || m[2] !== undefined) {
      out.push(
        <strong key={key} className="font-semibold text-ink">
          {m[1] ?? m[2]}
        </strong>,
      )
    } else if (m[3] !== undefined) {
      out.push(
        <em key={key} className="italic">
          {m[3]}
        </em>,
      )
    } else if (m[4] !== undefined) {
      out.push(
        <code
          key={key}
          className="rounded-[4px] bg-sunken px-1 py-px font-mono text-[0.82em] text-ink"
        >
          {m[4]}
        </code>,
      )
    } else if (m[5] !== undefined) {
      const n = Number(m[5])
      const citation = ctx.citations.find((c) => c.n === n)
      out.push(
        citation ? (
          <CitationChip
            key={key}
            citation={citation}
            excerpt={ctx.excerpts.find((e) => e.spanId === citation.spanId)}
          />
        ) : (
          // The marker is written before its citation event lands.
          <span
            key={key}
            aria-hidden
            className="mx-px inline-block h-[1.1em] w-[1.3em] translate-y-[-0.1em] rounded-[4px] bg-navy-50 align-middle"
          />
        ),
      )
    } else if (m[6] !== undefined) {
      out.push(
        <a
          key={key}
          href={m[7]}
          target="_blank"
          rel="noreferrer"
          className="text-navy-600 underline underline-offset-2"
        >
          {m[6]}
        </a>,
      )
    }
    last = at + m[0].length
  }

  if (last < text.length) out.push(text.slice(last))
  return out
}

/** Inline-only: table cells, field values, anywhere a block would be wrong. */
export function InlineMarkdown({
  md,
  citations = [],
  excerpts = [],
  className,
}: {
  md: string
  citations?: Citation[]
  excerpts?: Excerpt[]
  className?: string
}) {
  return <span className={className}>{parseInline(md, { citations, excerpts }, 'i')}</span>
}

const H = {
  1: 'mt-5 text-base font-semibold tracking-[-0.01em] text-ink',
  2: 'mt-5 text-[0.95rem] font-semibold tracking-[-0.01em] text-ink',
  3: 'mt-4 text-sm font-semibold text-ink',
  4: 'mt-3.5 text-[0.82rem] font-semibold uppercase tracking-wider text-ink-2',
} as const

export function Markdown({
  md,
  citations = [],
  excerpts = [],
  className,
  streaming,
}: {
  md: string
  citations?: Citation[]
  excerpts?: Excerpt[]
  className?: string
  /** Masks syntax that is still arriving, so no raw `**` reaches the reader. */
  streaming?: boolean
}) {
  const ctx: Ctx = { citations, excerpts }
  const lines = (streaming ? maskStreamingMarkdown(md) : md).split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    /* fenced code */
    if (line.startsWith('```')) {
      const body: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++])
      i++
      blocks.push(
        <pre
          key={key++}
          className="mt-3 overflow-x-auto rounded-card border border-line bg-sunken p-3 font-mono text-xs leading-relaxed text-ink"
        >
          <code>{body.join('\n')}</code>
        </pre>,
      )
      continue
    }

    /* horizontal rule */
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push(<hr key={key++} className="mt-4 border-0 border-t border-line" />)
      i++
      continue
    }

    /* heading */
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      const level = Math.min(4, h[1].length) as 1 | 2 | 3 | 4
      const Tag = (['h3', 'h4', 'h5', 'h6'] as const)[level - 1]
      blocks.push(
        <Tag key={key++} className={cn('font-sans first:mt-0', H[level])}>
          {parseInline(h[2], ctx, `h${key}`)}
        </Tag>,
      )
      i++
      continue
    }

    /* table */
    if (line.trimStart().startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trimStart().startsWith('|')) {
        const cells = lines[i]
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((c) => c.trim())
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells)
        i++
      }
      const [head, ...body] = rows
      blocks.push(
        <div key={key++} className="mt-3.5 overflow-x-auto rounded-card border border-line">
          <table className="w-full border-collapse font-sans text-xs">
            {head && (
              <thead>
                <tr className="bg-sunken">
                  {head.map((c, ci) => (
                    <th
                      key={ci}
                      scope="col"
                      className="border-b border-line px-2.5 py-2 text-left font-semibold text-ink"
                    >
                      {parseInline(c, ctx, `th${ci}`)}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {body.map((r, ri) => (
                <tr key={ri} className="border-b border-line last:border-0">
                  {r.map((c, ci) => (
                    <td key={ci} className="px-2.5 py-2 align-top text-ink-2">
                      {parseInline(c, ctx, `td${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    /* blockquote */
    if (line.trimStart().startsWith('>')) {
      const body: string[] = []
      while (i < lines.length && lines[i].trimStart().startsWith('>')) {
        body.push(lines[i].trimStart().replace(/^>\s?/, ''))
        i++
      }
      blocks.push(
        <blockquote
          key={key++}
          className="mt-3.5 border-l-2 border-navy-200 bg-navy-50/40 py-2 pl-3.5 pr-3 text-[0.95em] text-ink-2"
        >
          {parseInline(body.join(' '), ctx, `bq${key}`)}
        </blockquote>,
      )
      continue
    }

    /* lists */
    const bullet = /^\s*[-*+]\s+(.*)$/
    const ordered = /^\s*(\d+)[.)]\s+(.*)$/
    if (bullet.test(line) || ordered.test(line)) {
      const isOrdered = ordered.test(line)
      const items: string[] = []
      while (i < lines.length) {
        const m = isOrdered ? ordered.exec(lines[i]) : bullet.exec(lines[i])
        if (m) {
          items.push(isOrdered ? m[2] : m[1])
          i++
        } else if (lines[i].startsWith('  ') && items.length > 0 && lines[i].trim()) {
          items[items.length - 1] += ` ${lines[i].trim()}`
          i++
        } else break
      }
      const List = isOrdered ? 'ol' : 'ul'
      blocks.push(
        <List
          key={key++}
          className={cn(
            'mt-3 space-y-1.5 pl-5',
            isOrdered ? 'list-decimal' : 'list-disc',
            'marker:text-ink-3',
          )}
        >
          {items.map((it, ii) => (
            <li key={ii} className="pl-0.5">
              {parseInline(it, ctx, `li${key}-${ii}`)}
            </li>
          ))}
        </List>,
      )
      continue
    }

    /* paragraph */
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith('```') &&
      !/^(#{1,6})\s/.test(lines[i]) &&
      !lines[i].trimStart().startsWith('|') &&
      !lines[i].trimStart().startsWith('>') &&
      !bullet.test(lines[i]) &&
      !ordered.test(lines[i]) &&
      !/^(-{3,}|\*{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      para.push(lines[i])
      i++
    }
    blocks.push(
      <p key={key++} className="mt-3 first:mt-0">
        {parseInline(para.join(' '), ctx, `p${key}`)}
      </p>,
    )
  }

  return (
    <div
      className={cn(
        // Answers are work product: serif body, sans headings and tables.
        'font-serif text-[0.9375rem] leading-[1.68] text-ink [&>*:first-child]:mt-0',
        className,
      )}
    >
      {blocks.map((b, bi) => (
        <Fragment key={bi}>{b}</Fragment>
      ))}
    </div>
  )
}
