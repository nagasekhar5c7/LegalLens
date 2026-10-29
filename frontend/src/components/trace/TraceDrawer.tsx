import { useEffect, useState } from 'react'
import { Activity, Check, Copy, Cpu, DollarSign, Hash, Search, Timer } from 'lucide-react'
import type { AssistantTurn, Trace } from '@/api/types'
import { api } from '@/api/client'
import { formatMs, formatTokens, formatUsd } from '@/lib/format'
import { copyText } from '@/lib/exporting'
import { toast } from '@/store/useToasts'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Dialog, SheetContent } from '@/components/ui/Overlay'
import { Progress } from '@/components/ui/Feedback'
import { useUi } from '@/store/useUi'
import { useConversations } from '@/store/useConversations'

/**
 * NFR-11 / NFR-12 made visible: stage timings, cost, and the exact model,
 * prompt and index versions that produced this answer.
 *
 * This is what makes an answer reproducible, and it is the surface an attorney
 * needs under the duty of technological competence — not a black box.
 */
export function TraceDrawer() {
  const { traceTurnId, setTraceTurnId } = useUi()
  const turns = useConversations((s) => s.turns)
  const turn = turns.find((t) => t.id === traceTurnId && t.role === 'assistant') as
    | AssistantTurn
    | undefined

  /**
   * NFR-11: the stored trace adds what the stream does not carry — the queries
   * actually run and the candidate set with its scores, including the
   * candidates the reranker dropped. That last part is the whole value: a
   * retrieval failure is diagnosable from here without re-running the query.
   *
   * Fetched when the drawer opens rather than with the turn: it is a diagnostic
   * nobody pays for until they ask.
   */
  const traceId = turn?.traceId
  const [trace, setTrace] = useState<Trace | null>(null)

  useEffect(() => {
    if (!traceId) {
      setTrace(null)
      return
    }
    let live = true
    void api
      .getTrace(traceId)
      .then((t) => {
        if (live) setTrace(t)
      })
      .catch(() => {
        // The drawer is still useful without it; everything above this point
        // came down with the turn.
        if (live) setTrace(null)
      })
    return () => {
      live = false
    }
  }, [traceId])

  return (
    <Dialog open={!!traceTurnId} onOpenChange={(o) => !o && setTraceTurnId(null)}>
      {turn && (
        <SheetContent
          title="Response trace"
          description="Everything behind this answer: what ran, how long it took, what it cost, and which versions produced it."
        >
          <div className="space-y-5 p-4">
            {turn.usage && (
              <section>
                <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                  Totals
                </h3>
                <dl className="mt-2 grid grid-cols-2 gap-2">
                  <Metric
                    icon={<Timer aria-hidden className="size-3.5" />}
                    label="Time to first token"
                    value={formatMs(turn.usage.timeToFirstTokenMs)}
                    note="budget 2s p95"
                    ok={turn.usage.timeToFirstTokenMs <= 2000}
                  />
                  <Metric
                    icon={<Activity aria-hidden className="size-3.5" />}
                    label="Total latency"
                    value={formatMs(turn.usage.latencyMs)}
                    note={turn.kind === 'answer' ? 'budget 15s p95' : 'budget 3min'}
                    ok={
                      turn.kind === 'answer'
                        ? turn.usage.latencyMs <= 15_000
                        : turn.usage.latencyMs <= 180_000
                    }
                  />
                  <Metric
                    icon={<DollarSign aria-hidden className="size-3.5" />}
                    label="Cost"
                    value={formatUsd(turn.usage.usd)}
                    note={turn.kind === 'answer' ? 'budget $0.05' : 'per document'}
                    ok={turn.kind !== 'answer' || turn.usage.usd <= 0.05}
                  />
                  <Metric
                    icon={<Hash aria-hidden className="size-3.5" />}
                    label="Tokens"
                    value={`${formatTokens(turn.usage.inputTokens)} in · ${formatTokens(turn.usage.outputTokens)} out`}
                  />
                </dl>
              </section>
            )}

            <section>
              <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                Pipeline
              </h3>
              <ol className="mt-2 space-y-1">
                {turn.stages.map((s, i) => {
                  const prev = i === 0 ? 0 : turn.stages[i - 1].elapsedMs
                  const span = Math.max(0, s.elapsedMs - prev)
                  const total = turn.usage?.latencyMs ?? s.elapsedMs
                  return (
                    <li key={`${s.key}-${i}`} className="rounded-control bg-canvas px-2.5 py-1.5">
                      <div className="flex items-baseline gap-2">
                        <Check aria-hidden className="size-3 shrink-0 text-grounded" />
                        <span className="shrink-0 text-xs font-medium text-ink">{s.label}</span>
                        <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-3">
                          {formatMs(span)}
                        </span>
                      </div>
                      {s.detail && (
                        <p className="ml-5 mt-0.5 text-2xs text-ink-2">{s.detail}</p>
                      )}
                      <Progress value={(span / Math.max(1, total)) * 100} className="ml-5 mt-1.5" />
                    </li>
                  )
                })}
              </ol>
            </section>

            {trace && (trace.queries.length > 0 || trace.candidates.length > 0) && (
              <section>
                <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                  Retrieval
                </h3>
                {trace.queries.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {trace.queries.map((q, i) => (
                      <li
                        key={`${q}-${i}`}
                        className="flex items-baseline gap-2 rounded-control bg-canvas px-2.5 py-1.5"
                      >
                        <Search aria-hidden className="size-3 shrink-0 text-ink-3" />
                        <span className="min-w-0 font-mono text-[10px] leading-relaxed text-ink-2">
                          {q}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {trace.candidates.length > 0 && (
                  <>
                    <p className="mt-2 text-2xs text-ink-3">
                      {trace.candidates.length} candidate
                      {trace.candidates.length === 1 ? '' : 's'} scored ·{' '}
                      {trace.candidates.filter((c) => c.kept).length} kept above the floor
                    </p>
                    <ul className="mt-1.5 divide-y divide-line rounded-card border border-line">
                      {trace.candidates.map((c) => (
                        <li
                          key={c.spanId}
                          className="flex items-baseline gap-2 px-2.5 py-1.5"
                        >
                          <span className="shrink-0 font-mono text-[10px] text-ink-3">
                            p. {c.page}
                          </span>
                          <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink-2">
                            {c.spanId}
                          </span>
                          <Badge tone={c.kept ? 'grounded' : 'neutral'} className="h-4 px-1.5">
                            {c.score.toFixed(2)}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </section>
            )}

            <section>
              <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                Grounding
              </h3>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge tone={turn.citations.length > 0 ? 'grounded' : 'neutral'}>
                  {turn.citations.length} citations resolved
                </Badge>
                <Badge tone="neutral">{turn.excerpts.length} passages retrieved</Badge>
                {turn.scopeUsed && (
                  <Badge tone="neutral">
                    {turn.scopeUsed.documentCount} docs · {turn.scopeUsed.pageCount} pages
                  </Badge>
                )}
                {turn.refusal && <Badge tone="refusal">refused — below floor</Badge>}
              </div>
              <p className="mt-2 text-2xs leading-relaxed text-ink-3">
                Citation verification ran over this output. An unresolvable citation is stripped or
                the output is refused — shipping one is a release-blocking defect.
              </p>
            </section>

            {turn.versions && (
              <section>
                <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                  Versions
                </h3>
                <dl className="mt-2 divide-y divide-line rounded-card border border-line">
                  {[
                    ['Model', turn.versions.model],
                    ['Prompt', turn.versions.prompt],
                    ['Index', turn.versions.index],
                    ['Trace id', turn.traceId ?? '—'],
                  ].map(([k, v]) => (
                    <div key={k} className="flex items-baseline gap-2 px-2.5 py-1.5">
                      <dt className="shrink-0 text-2xs text-ink-3">{k}</dt>
                      <dd className="ml-auto min-w-0 truncate font-mono text-[10px] text-ink-2">
                        {v}
                      </dd>
                    </div>
                  ))}
                </dl>
                <Button
                  size="xs"
                  variant="secondary"
                  className="mt-2 w-full"
                  onClick={async () => {
                    const ok = await copyText(
                      JSON.stringify(
                        {
                          traceId: turn.traceId,
                          versions: turn.versions,
                          usage: turn.usage,
                          stages: turn.stages,
                          citations: turn.citations.map((c) => c.spanId),
                        },
                        null,
                        2,
                      ),
                    )
                    if (ok) toast.done('Trace copied as JSON')
                  }}
                >
                  <Copy aria-hidden className="size-3.5" />
                  Copy trace as JSON
                </Button>
              </section>
            )}

            <p className="flex items-start gap-1.5 border-t border-line pt-3 text-[10px] leading-relaxed text-ink-3">
              <Cpu aria-hidden className="mt-px size-3 shrink-0" />
              Pinning the model, prompt and index versions is what makes this answer reproducible —
              re-running the same trace against the same versions should produce the same output.
            </p>
          </div>
        </SheetContent>
      )}
    </Dialog>
  )
}

function Metric({
  icon,
  label,
  value,
  note,
  ok,
}: {
  icon: React.ReactNode
  label: string
  value: string
  note?: string
  ok?: boolean
}) {
  return (
    <div className="rounded-card border border-line bg-canvas px-2.5 py-2">
      <dt className="flex items-center gap-1.5 text-[10px] text-ink-3">
        {icon}
        {label}
      </dt>
      <dd className="mt-1 font-mono text-xs font-medium text-ink">{value}</dd>
      {note && (
        <p
          className={
            ok === undefined
              ? 'mt-0.5 text-[10px] text-ink-3'
              : ok
                ? 'mt-0.5 text-[10px] text-grounded'
                : 'mt-0.5 text-[10px] text-caution'
          }
        >
          {ok === undefined ? note : ok ? `within ${note}` : `over ${note}`}
        </p>
      )}
    </div>
  )
}
