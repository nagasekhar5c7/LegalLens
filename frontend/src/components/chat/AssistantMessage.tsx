import { Scale } from 'lucide-react'
import type { AssistantTurn, Mode, RefusalSuggestion } from '@/api/types'
import { cn } from '@/lib/cn'
import { absoluteTime } from '@/lib/format'
import { Markdown } from './Markdown'
import { StageTrace } from './StageTrace'
import { ScopeStatement } from './ScopeStatement'
import { IntentBadge } from './IntentBadge'
import { RefusalCard } from './RefusalCard'
import { ErrorCard, IncompleteBanner } from './ErrorCard'
import { MessageActions } from './MessageActions'
import { SummaryView } from '@/components/answer/SummaryView'
import { DraftView } from '@/components/answer/DraftView'
import { WorkProductNote } from '@/components/answer/DraftBanner'
import { TEMPLATE_FIELDS } from '@/lib/templates'
import { useUi } from '@/store/useUi'
import { useConversations } from '@/store/useConversations'
import { useEvidence } from '@/store/useEvidence'

export function AssistantMessage({
  turn,
  onSuggestion,
}: {
  turn: AssistantTurn
  onSuggestion?: (s: RefusalSuggestion) => void
}) {
  const streaming = turn.status === 'streaming'
  const { setEvidenceOpen } = useUi()
  const { regenerate } = useConversations()
  const setTurnEvidence = useEvidence((s) => s.setTurn)

  function showExcerpts() {
    setTurnEvidence(turn.id, turn.citations, turn.excerpts)
    setEvidenceOpen(true)
  }

  return (
    <article
      aria-label="Legal Lens response"
      className="group/turn px-4 py-5 sm:px-6"
      onMouseEnter={() => {
        if (turn.citations.length > 0) setTurnEvidence(turn.id, turn.citations, turn.excerpts)
      }}
    >
      <div className="mx-auto flex max-w-3xl gap-3">
        <span
          aria-hidden
          className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-navy-200 bg-navy-50"
        >
          <Scale className="size-3.5 text-navy-600" />
        </span>

        <div className="min-w-0 flex-1">
          <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <IntentBadge
              requestedMode={turn.requestedMode}
              intent={turn.intent}
              onRerun={streaming ? undefined : (m: Mode) => void regenerate(turn.id, m)}
            />
            {turn.scopeUsed && <ScopeStatement scope={turn.scopeUsed} />}
            <time
              dateTime={turn.createdAt}
              className="ml-auto shrink-0 text-2xs text-ink-3 opacity-0 transition-opacity group-hover/turn:opacity-100"
            >
              {absoluteTime(turn.createdAt)}
            </time>
          </header>

          {turn.stages.length > 0 && (
            <StageTrace stages={turn.stages} streaming={streaming} className="mt-2.5" />
          )}

          {/* aria-live so a screen-reader user is told what is happening and
              when it finishes, rather than being left in silence (NFR-13). */}
          <div
            aria-live="polite"
            aria-atomic="false"
            className={cn('mt-3', turn.status === 'error' && 'mt-2.5')}
          >
            {turn.refusal ? (
              <RefusalCard refusal={turn.refusal} onSuggestion={onSuggestion} />
            ) : turn.kind === 'summary' && turn.summary ? (
              <SummaryView
                summary={turn.summary}
                citations={turn.citations}
                excerpts={turn.excerpts}
                coverage={turn.coverage}
                streaming={streaming}
                expectedFields={
                  turn.expectedFieldCount ?? TEMPLATE_FIELDS[turn.summary.documentType]?.length
                }
              />
            ) : turn.kind === 'draft' && turn.draft ? (
              <DraftView
                draft={turn.draft}
                citations={turn.citations}
                excerpts={turn.excerpts}
                streaming={streaming}
              />
            ) : turn.text ? (
              <>
                <Markdown
                  md={turn.text}
                  citations={turn.citations}
                  excerpts={turn.excerpts}
                  streaming={streaming}
                />
                {streaming && (
                  <span
                    aria-hidden
                    className="ml-0.5 inline-block h-[1.05em] w-0.5 translate-y-[0.15em] bg-navy-600"
                    style={{ animation: 'll-caret 1.1s steps(2, start) infinite' }}
                  />
                )}
              </>
            ) : null}

            {turn.error && (
              <div className={cn(turn.text && 'mt-3')}>
                <ErrorCard
                  error={turn.error}
                  onRetry={() => void regenerate(turn.id)}
                  onShowExcerpts={turn.excerpts.length > 0 ? showExcerpts : undefined}
                />
              </div>
            )}

            {turn.status === 'incomplete' && <IncompleteBanner />}
          </div>

          {!streaming && turn.status !== 'error' && turn.kind !== 'draft' && !turn.refusal && (
            <WorkProductNote />
          )}

          {!streaming && <MessageActions turn={turn} />}
        </div>
      </div>
    </article>
  )
}
