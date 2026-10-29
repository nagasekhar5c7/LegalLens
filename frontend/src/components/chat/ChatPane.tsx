import { useEffect, useRef, useState } from 'react'
import { ArrowDown, FileUp, Scale } from 'lucide-react'
import type { AssistantTurn, LegalDocument, RefusalSuggestion, Turn } from '@/api/types'
import { cn } from '@/lib/cn'
import { Button, IconButton } from '@/components/ui/Button'
import { AssistantMessage } from './AssistantMessage'
import { UserMessage } from './UserMessage'
import { SuggestionChips } from '@/components/composer/SuggestionChips'

export function ChatPane({
  turns,
  documents,
  onSuggestion,
  onResend,
  onUpload,
}: {
  turns: Turn[]
  documents: LegalDocument[]
  onSuggestion: (s: RefusalSuggestion) => void
  onResend: (text: string) => void
  onUpload: () => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)

  /* Stick to the bottom while streaming, unless the reader has scrolled up to
     verify something — then leave them where they are and offer a button. */
  useEffect(() => {
    const el = scroller.current
    if (!el || !pinned) return
    el.scrollTop = el.scrollHeight
  }, [turns, pinned])

  function onScroll() {
    const el = scroller.current
    if (!el) return
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const ready = documents.filter((d) => d.status === 'ready')

  if (turns.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-4">
        <div className="w-full max-w-2xl py-10">
          <div className="text-center">
            <span
              aria-hidden
              className="mx-auto grid size-11 place-items-center rounded-full border border-navy-200 bg-navy-50"
            >
              <Scale className="size-5 text-navy-600" />
            </span>
            <h1 className="mt-4 text-lg font-semibold tracking-[-0.015em] text-ink">
              {ready.length > 0
                ? 'Question, summarise or draft from your documents'
                : 'Add a document to begin'}
            </h1>
            <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-ink-2">
              {ready.length > 0 ? (
                <>
                  Every answer is grounded in the {ready.length} document
                  {ready.length === 1 ? '' : 's'} in your workspace, with a citation behind each
                  claim. If the answer is not in them, Legal Lens says so.
                </>
              ) : (
                <>
                  Legal Lens answers only from documents you upload — it has no general knowledge of
                  statutes or case law, by design. Add a PDF, DOCX or TXT to start.
                </>
              )}
            </p>
          </div>

          {ready.length > 0 ? (
            <SuggestionChips documents={documents} className="mt-7" />
          ) : (
            <div className="mt-6 flex justify-center">
              <Button variant="primary" size="md" onClick={onUpload}>
                <FileUp aria-hidden className="size-4" />
                Upload documents
              </Button>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        {turns.map((turn, i) =>
          turn.role === 'user' ? (
            <UserMessage
              key={turn.id}
              turn={turn}
              onResend={i === turns.length - 2 ? onResend : undefined}
            />
          ) : (
            <AssistantMessage key={turn.id} turn={turn as AssistantTurn} onSuggestion={onSuggestion} />
          ),
        )}
        <div className="h-4" />
      </div>

      <div
        className={cn(
          'pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 transition-opacity',
          pinned ? 'opacity-0' : 'opacity-100',
        )}
      >
        <IconButton
          label="Jump to the latest message"
          variant="secondary"
          size="md"
          tabIndex={pinned ? -1 : 0}
          className="pointer-events-auto rounded-full shadow-md"
          onClick={() => {
            setPinned(true)
            const el = scroller.current
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
          }}
        >
          <ArrowDown className="size-4" />
        </IconButton>
      </div>
    </div>
  )
}
