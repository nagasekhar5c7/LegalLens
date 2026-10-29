import { CircleSlash, Flag, Layers3, Quote, TriangleAlert } from 'lucide-react'
import type { Citation, Coverage, Excerpt, SummaryField, SummaryPayload } from '@/api/types'
import { cn } from '@/lib/cn'
import { shortDocName } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Feedback'
import { Markdown } from '@/components/chat/Markdown'
import { CitationChip } from '@/components/chat/CitationChip'

/**
 * FR-15 / FR-16, and BRD §9.
 *
 * Field cards against the template for the detected type. The field-level
 * structure is what makes Summary measurable — field precision, instead of a
 * judgment about whether a paragraph of prose was "good".
 */
export function SummaryView({
  summary,
  citations,
  excerpts,
  coverage,
  streaming,
  expectedFields,
}: {
  summary: SummaryPayload
  citations: Citation[]
  excerpts: Excerpt[]
  coverage?: Coverage
  streaming?: boolean
  expectedFields?: number
}) {
  const fields = summary.fields.filter(Boolean)
  const pending = Math.max(0, (expectedFields ?? fields.length) - fields.length)

  return (
    <div>
      <header className="flex flex-wrap items-center gap-2 border-b border-line pb-2.5">
        <Layers3 aria-hidden className="size-4 shrink-0 text-ink-3" />
        <p className="min-w-0 flex-1 truncate text-xs font-semibold text-ink">
          {shortDocName(summary.documentName, 52)}
        </p>
        <Badge tone="navy">{summary.templateName}</Badge>
      </header>

      {coverage && <CoverageNote coverage={coverage} />}

      <dl className="mt-1">
        {fields.map((field) => (
          <SummaryFieldRow
            key={field.key}
            field={field}
            citations={citations}
            excerpts={excerpts}
          />
        ))}
      </dl>

      {streaming && pending > 0 && (
        <div className="mt-3 space-y-3" aria-label={`${pending} fields still being extracted`}>
          {Array.from({ length: Math.min(3, pending) }).map((_, i) => (
            <div key={i} className="border-t border-line pt-3">
              <Skeleton className="h-3 w-40" />
              <Skeleton className="mt-2 h-3 w-full" />
              <Skeleton className="mt-1.5 h-3 w-4/5" />
            </div>
          ))}
          <p className="text-2xs text-ink-3">
            {pending} field{pending === 1 ? '' : 's'} still being extracted…
          </p>
        </div>
      )}
    </div>
  )
}

function SummaryFieldRow({
  field,
  citations,
  excerpts,
}: {
  field: SummaryField
  citations: Citation[]
  excerpts: Excerpt[]
}) {
  const cites = field.citations
    .map((n) => citations.find((c) => c.n === n))
    .filter((c): c is Citation => !!c)

  return (
    <div className="border-t border-line py-3.5 first:border-0">
      <dt className="flex flex-wrap items-center gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
          {field.label}
        </span>
        {field.flag === 'off_market' && (
          <Badge tone="caution">
            <Flag aria-hidden className="size-2.5" />
            Off-market
          </Badge>
        )}
        {field.flag === 'unusual' && (
          <Badge tone="caution">
            <TriangleAlert aria-hidden className="size-2.5" />
            Worth attention
          </Badge>
        )}
        {cites.length > 0 && (
          <span className="ml-auto flex shrink-0 items-center gap-1">
            <Quote aria-hidden className="size-2.5 text-ink-3" />
            {cites.map((c) => (
              <CitationChip
                key={c.n}
                citation={c}
                excerpt={excerpts.find((e) => e.spanId === c.spanId)}
              />
            ))}
          </span>
        )}
      </dt>

      <dd className="mt-1.5">
        {field.valueMd === null ? (
          <NotAddressed />
        ) : (
          <>
            <Markdown md={field.valueMd} citations={citations} excerpts={excerpts} />
            {field.flagNote && (
              <p className="mt-2 flex items-start gap-1.5 rounded-control border border-caution-line bg-caution-bg px-2.5 py-1.5 font-sans text-2xs leading-relaxed text-caution">
                <TriangleAlert aria-hidden className="mt-px size-3 shrink-0" />
                {field.flagNote}
              </p>
            )}
          </>
        )}
      </dd>
    </div>
  )
}

/**
 * FR-16: a genuinely absent field is a legitimate outcome, not missing data.
 * Neutral type, no warning colour, no empty-looking dash — because an inferred
 * value here would be exactly the failure the product exists to prevent.
 */
function NotAddressed() {
  return (
    <p className="inline-flex items-center gap-1.5 rounded-control bg-sunken px-2 py-1 font-sans text-xs italic text-ink-3">
      <CircleSlash aria-hidden className="size-3 shrink-0" />
      Not addressed in this document
    </p>
  )
}

/** FR-17: say what was covered, rather than truncating silently. */
function CoverageNote({ coverage }: { coverage: Coverage }) {
  const full = coverage.pagesCovered >= coverage.totalPages
  return (
    <p
      className={cn(
        'mt-2.5 flex items-start gap-2 rounded-control border px-2.5 py-2 text-2xs leading-relaxed',
        full ? 'border-grounded-line bg-grounded-bg text-grounded' : 'border-caution-line bg-caution-bg text-caution',
      )}
    >
      <Layers3 aria-hidden className="mt-px size-3 shrink-0" />
      <span>
        <span className="font-semibold">
          {coverage.pagesCovered} of {coverage.totalPages} pages · {coverage.passes} passes.
        </span>{' '}
        {coverage.note}
      </span>
    </p>
  )
}
