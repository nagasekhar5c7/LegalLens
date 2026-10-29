import { useEffect, useRef } from 'react'
import { ArrowUp, Info, Paperclip, Square } from 'lucide-react'
import type { LegalDocument } from '@/api/types'
import { cn } from '@/lib/cn'
import { MOD, useHotkey } from '@/lib/hotkeys'
import { shortDocName } from '@/lib/format'
import { Button, IconButton } from '@/components/ui/Button'
import { Select } from '@/components/ui/Field'
import { Kbd } from '@/components/ui/Feedback'
import { Tooltip } from '@/components/ui/Floating'
import { useComposer, composerBlocker } from '@/store/useComposer'
import { useUi } from '@/store/useUi'
import { ModeChips } from './ModeChips'
import { ScopePicker } from './ScopePicker'
import { DraftParamsDrawer } from './DraftParamsDrawer'

const PLACEHOLDER: Record<string, string> = {
  auto: 'Ask about your documents, or say what you need drafted…',
  ask: 'Where does this agreement address assignment on a change of control?',
  summary: 'Add anything the summary should pay particular attention to (optional)…',
  draft: 'Add any instruction beyond the draft settings (optional)…',
}

export function PromptBar({
  documents,
  streaming,
  onSend,
  onStop,
}: {
  documents: LegalDocument[]
  streaming: boolean
  onSend: () => void
  onStop: () => void
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const {
    text,
    mode,
    scope,
    draftParams,
    summaryDocumentId,
    setText,
    setMode,
    setScope,
    setSummaryDocumentId,
    patchDraftParams,
  } = useComposer()
  const setUploadOpen = useUi((s) => s.setUploadOpen)

  const ready = documents.filter((d) => d.status === 'ready')
  const blocker = composerBlocker(mode, text, scope, draftParams, summaryDocumentId, ready.length)
  const canSend = !streaming && !blocker && (mode === 'draft' || mode === 'summary' || !!text.trim())

  /* Auto-grow to a ceiling, then scroll. */
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 248)}px`
  }, [text])

  useHotkey('/', (e) => {
    e.preventDefault()
    ref.current?.focus()
  })
  useHotkey('mod+1', () => setMode('auto'), { allowInEditable: true })
  useHotkey('mod+2', () => setMode('ask'), { allowInEditable: true })
  useHotkey('mod+3', () => setMode('summary'), { allowInEditable: true })
  useHotkey('mod+4', () => setMode('draft'), { allowInEditable: true })

  // Default Summary to the first ready document so the mode is never dead.
  useEffect(() => {
    if (mode === 'summary' && !summaryDocumentId && ready.length > 0) {
      setSummaryDocumentId(ready[0].id)
    }
  }, [mode, summaryDocumentId, ready, setSummaryDocumentId])

  const summaryDoc = ready.find((d) => d.id === summaryDocumentId)

  return (
    <div className="px-4 pb-4 pt-1 sm:px-6 print:hidden">
      <div className="mx-auto max-w-3xl">
        {blocker && (
          <p
            role="status"
            className="mb-2 flex items-start gap-1.5 rounded-control border border-caution-line bg-caution-bg px-2.5 py-1.5 text-2xs leading-relaxed text-caution"
          >
            <Info aria-hidden className="mt-px size-3.5 shrink-0" />
            {blocker}
          </p>
        )}

        <div
          className={cn(
            'rounded-panel border bg-raised shadow-sm transition-[border-color,box-shadow]',
            'focus-within:border-navy-600 focus-within:ring-2 focus-within:ring-navy-600/15',
            blocker ? 'border-caution-line' : 'border-line-strong',
          )}
        >
          {mode === 'summary' && (
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-2.5 py-2">
              <label htmlFor="summary-doc" className="shrink-0 text-2xs font-medium text-ink-2">
                Summarise
              </label>
              <Select
                id="summary-doc"
                className="h-7 max-w-72 flex-1 text-xs"
                value={summaryDocumentId ?? ''}
                onChange={(e) => setSummaryDocumentId(e.target.value || null)}
                options={[
                  { value: '', label: 'Choose a document…' },
                  ...ready.map((d) => ({ value: d.id, label: shortDocName(d.name, 44) })),
                ]}
              />
              {summaryDoc && (
                <span className="text-2xs text-ink-3">
                  {summaryDoc.type} template · {summaryDoc.pages} pages
                </span>
              )}
            </div>
          )}

          <label htmlFor="prompt" className="sr-only">
            Your question or instruction
          </label>
          <textarea
            id="prompt"
            ref={ref}
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                if (canSend) onSend()
              }
            }}
            placeholder={PLACEHOLDER[mode]}
            className={cn(
              'block max-h-62 w-full resize-none bg-transparent px-3 pb-1 pt-2.5',
              'text-sm leading-relaxed text-ink placeholder:text-ink-3 focus:outline-none',
            )}
          />

          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-2 pb-2 pt-1">
            <ModeChips mode={mode} onChange={setMode} disabled={streaming} />

            <span className="mx-0.5 hidden h-4 w-px bg-line sm:block" aria-hidden />

            {mode === 'draft' ? (
              <DraftParamsDrawer
                params={draftParams}
                documents={documents}
                onChange={patchDraftParams}
                onGenerate={onSend}
              />
            ) : mode === 'summary' ? null : (
              <ScopePicker documents={documents} scope={scope} onChange={setScope} />
            )}

            <div className="ml-auto flex items-center gap-1.5">
              <Tooltip content="Upload documents" shortcut={`${MOD} U`}>
                <IconButton
                  label="Upload documents"
                  size="sm"
                  variant="quiet"
                  onClick={() => setUploadOpen(true)}
                >
                  <Paperclip className="size-4" />
                </IconButton>
              </Tooltip>

              {streaming ? (
                <Button size="sm" variant="secondary" onClick={onStop}>
                  <Square className="size-3" fill="currentColor" />
                  Stop
                </Button>
              ) : (
                <Tooltip content="Send" shortcut={`${MOD} ↵`}>
                  <IconButton
                    label="Send"
                    size="sm"
                    variant="primary"
                    disabled={!canSend}
                    onClick={onSend}
                  >
                    <ArrowUp className="size-4" strokeWidth={2.5} />
                  </IconButton>
                </Tooltip>
              )}
            </div>
          </div>
        </div>

        <p className="mt-1.5 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-[10px] text-ink-3">
          <span>
            <Kbd>{MOD}</Kbd> <Kbd>↵</Kbd> to send · <Kbd>↵</Kbd> for a new line
          </span>
          <span aria-hidden>·</span>
          <span>Answers come only from your documents, with a citation behind every claim.</span>
        </p>
      </div>
    </div>
  )
}
