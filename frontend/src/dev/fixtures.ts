import type { Citation, Excerpt, Refusal } from '@/api/types'
import { citationLabel } from '@/lib/format'

/**
 * Sample data for `/kitchen-sink` — the component gallery, and nothing else.
 *
 * This is the only fixture file left in the frontend. `src/mocks/` is gone: the
 * corpus and the scripted answers now live in `backend/app/ai/fixtures/`, where
 * they are served over the wire, so a second copy here would be a second source
 * of truth. The gallery still needs *something* to render a `CitationChip` and
 * an `ExcerptCard` against without a signed-in session, and this is deliberately
 * the smallest thing that does — two real clauses from the seeded MSA, written
 * out by hand rather than reached through a mock API.
 */

interface SampleSpan {
  documentId: string
  documentName: string
  page: number
  section: string
  before: string
  text: string
  after: string
}

const SPANS: Record<string, SampleSpan> = {
  s_msa_1223: {
    documentId: 'doc_msa',
    documentName: 'Nexora–Arclight Master Services Agreement.pdf',
    page: 84,
    section: '§ 12.3',
    before:
      '12.2 Subcontractors. Provider may engage subcontractors to perform portions of the Services, provided that Provider remains responsible for their performance. 12.3 Assignment. ',
    text: 'Neither party may assign or otherwise transfer this Agreement, in whole or in part, whether by operation of law or otherwise, without the prior written consent of the other party; provided, however, that either party may assign this Agreement without consent to a successor in interest in connection with a merger, consolidation, reorganisation, or sale of all or substantially all of its assets or voting securities, except that Customer’s consent shall be required where the proposed assignee is a Restricted Competitor identified on Schedule 12.3.',
    after:
      ' Any purported assignment in violation of this Section shall be void. 12.4 Notices. All notices under this Agreement shall be in writing and delivered by hand, ',
  },
  s_msa_1226: {
    documentId: 'doc_msa',
    documentName: 'Nexora–Arclight Master Services Agreement.pdf',
    page: 85,
    section: '§ 12.6',
    before:
      '12.5 Severability. If any provision of this Agreement is held unenforceable, the remainder shall continue in effect. 12.6 Change of Control. ',
    text: 'For the avoidance of doubt, a transaction or series of related transactions resulting in the transfer of more than fifty percent (50%) of the voting securities of a party shall constitute a Change of Control, and the non-transferring party may terminate this Agreement upon thirty (30) days’ written notice delivered within sixty (60) days after receiving notice of such Change of Control.',
    after:
      ' Termination under this Section shall not relieve either party of obligations accrued before the effective date of termination. 12.7 Counterparts. ',
  },
}

/** Citation numbering and labels agree because both derive from the span. */
export function buildCitations(spanIds: string[]): Citation[] {
  return spanIds.flatMap((spanId, i) => {
    const span = SPANS[spanId]
    if (!span) return []
    const base = {
      n: i + 1,
      spanId,
      documentId: span.documentId,
      documentName: span.documentName,
      page: span.page,
      section: span.section,
    }
    return [{ ...base, label: citationLabel(base) }]
  })
}

/** FR-12: the excerpt is the same span, with its surrounding context. */
export function buildExcerpts(spanIds: string[], relevances?: number[]): Excerpt[] {
  return spanIds.flatMap((spanId, i) => {
    const span = SPANS[spanId]
    if (!span) return []
    return [
      {
        spanId,
        documentId: span.documentId,
        documentName: span.documentName,
        page: span.page,
        section: span.section,
        before: span.before,
        text: span.text,
        after: span.after,
        relevance: relevances?.[i] ?? Math.max(0.42, 0.93 - i * 0.09),
      },
    ]
  })
}

/** FR-14: a refusal states what was searched for and why it fell short. */
export function refusalFor(question: string, scopeDescription: string): Refusal {
  const topic = question.replace(/[?.!]+$/, '').slice(0, 90)
  return {
    kind: 'below_threshold',
    message: `Nothing in ${scopeDescription} addresses this. The closest passages scored below the relevance floor, so rather than assemble an answer from weakly-related text, Legal Lens is declining.`,
    lookedFor: [
      topic.toLowerCase(),
      `${topic.toLowerCase().split(' ').slice(0, 4).join(' ')} clause provision`,
      `definition of ${topic.toLowerCase().split(' ').slice(-3).join(' ')}`,
    ],
    bestRelevance: 0.18,
    threshold: 0.25,
    suggestions: [
      {
        kind: 'widen_scope',
        label: 'Search the whole workspace',
        detail: 'Currently scoped narrower than your full document set.',
      },
      {
        kind: 'upload',
        label: 'Upload the document that covers this',
        detail:
          'If the provision lives in a schedule or exhibit you have not added yet, it cannot be found.',
      },
      {
        kind: 'rephrase',
        label: 'Rephrase using the document’s own vocabulary',
        detail: 'Contracts often address a concept without using its common name.',
      },
    ],
  }
}
