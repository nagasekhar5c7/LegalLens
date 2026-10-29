import { FileText, PenLine, Search, SearchX } from 'lucide-react'
import type { LegalDocument, Mode } from '@/api/types'
import { cn } from '@/lib/cn'
import { useComposer } from '@/store/useComposer'

interface Suggestion {
  label: string
  text: string
  mode: Mode
  icon: React.ReactNode
  note?: string
  documentId?: string
}

/**
 * Starters drawn from the actual corpus, so a first-time user sees the product
 * work rather than guessing at it. The last one is deliberate: it demonstrates
 * a *correct refusal*, which is the behaviour hardest to believe until seen.
 */
function suggestionsFor(documents: LegalDocument[]): Suggestion[] {
  const has = (id: string) => documents.some((d) => d.id === id && d.status === 'ready')
  const out: Suggestion[] = []

  if (has('doc_msa')) {
    out.push({
      label: 'Assignment on a change of control',
      text: 'Does the MSA let Arclight assign the agreement to an acquirer without consent, and what happens on a change of control?',
      mode: 'ask',
      icon: <Search aria-hidden className="size-3.5" />,
    })
    out.push({
      label: 'What is the real liability cap?',
      text: 'What is the limitation of liability under the MSA? I need the actual operative cap.',
      mode: 'ask',
      icon: <Search aria-hidden className="size-3.5" />,
      note: 'crosses two documents',
    })
    out.push({
      label: 'Summarise the master services agreement',
      text: 'Summarise the master services agreement.',
      mode: 'summary',
      icon: <FileText aria-hidden className="size-3.5" />,
      documentId: 'doc_msa',
      note: '312 pages',
    })
  }
  if (has('doc_vance')) {
    out.push({
      label: 'What did Vance hold on arbitration?',
      text: 'What did the court hold about the arbitration clause in Vance?',
      mode: 'ask',
      icon: <Search aria-hidden className="size-3.5" />,
    })
    out.push({
      label: 'Draft a case brief for Vance',
      text: 'Give me a case brief for Vance v. Ridgeline.',
      mode: 'draft',
      icon: <PenLine aria-hidden className="size-3.5" />,
    })
  }
  out.push({
    label: 'Ask something the documents do not cover',
    text: 'What does the MSA say about force majeure and pandemic relief?',
    mode: 'ask',
    icon: <SearchX aria-hidden className="size-3.5" />,
    note: 'shows a refusal',
  })

  return out.slice(0, 6)
}

export function SuggestionChips({
  documents,
  className,
}: {
  documents: LegalDocument[]
  className?: string
}) {
  const { setText, setMode, setSummaryDocumentId, patchDraftParams } = useComposer()
  const suggestions = suggestionsFor(documents)
  if (suggestions.length === 0) return null

  return (
    <div className={cn('grid gap-2 sm:grid-cols-2', className)}>
      {suggestions.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => {
            setMode(s.mode)
            setText(s.text)
            if (s.mode === 'summary' && s.documentId) setSummaryDocumentId(s.documentId)
            if (s.mode === 'draft') {
              patchDraftParams({
                draftType: 'case_brief',
                questionPresented: s.text,
                documentIds: documents.filter((d) => d.id === 'doc_vance').map((d) => d.id),
              })
            }
            document.getElementById('prompt')?.focus()
          }}
          className="group flex items-start gap-2.5 rounded-card border border-line bg-raised px-3 py-2.5 text-left shadow-sm transition-colors hover:border-line-strong hover:bg-hover"
        >
          <span className="mt-px shrink-0 text-ink-3 group-hover:text-navy-600">{s.icon}</span>
          <span className="min-w-0 flex-1">
            <span className="block text-xs font-medium leading-snug text-ink">{s.label}</span>
            {s.note && <span className="mt-0.5 block text-2xs text-ink-3">{s.note}</span>}
          </span>
        </button>
      ))}
    </div>
  )
}
