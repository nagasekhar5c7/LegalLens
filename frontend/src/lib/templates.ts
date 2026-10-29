import type { DocType, DraftType } from '@/api/types'

/**
 * The shapes a summary and a draft are expected to arrive in.
 *
 * These are *presentational* constants, not fixtures: they are what the client
 * knows is coming while it is still streaming, so `SummaryView` can render nine
 * pending field rows and `DraftView` five pending section headings rather than
 * one growing list that reflows on every event.
 *
 * They mirror `backend/app/ai/fixtures/{summaries,drafts}.py` exactly, and the
 * authoritative values still come down the wire — `summary_start` carries the
 * real `templateName` and `fieldCount`, and each `summary_field` its own label.
 * BRD §9: a contract summary and an opinion summary share almost no structure,
 * so each document type has its own field set.
 */
export const TEMPLATE_FIELDS: Record<DocType, { key: string; label: string }[]> = {
  contract: [
    { key: 'parties', label: 'Parties and roles' },
    { key: 'term', label: 'Effective date, term, renewal' },
    { key: 'obligations', label: 'Core obligations per party' },
    { key: 'payment', label: 'Payment / consideration' },
    { key: 'termination', label: 'Termination rights and notice periods' },
    { key: 'liability', label: 'Limitation of liability and indemnity' },
    { key: 'assignment', label: 'Assignment and change of control' },
    { key: 'law', label: 'Governing law and dispute resolution' },
    { key: 'unusual', label: 'Unusual or off-market terms' },
  ],
  opinion: [
    { key: 'posture', label: 'Procedural posture' },
    { key: 'facts', label: 'Facts' },
    { key: 'issues', label: 'Issues presented' },
    { key: 'holding', label: 'Holding' },
    { key: 'reasoning', label: 'Reasoning' },
    { key: 'disposition', label: 'Disposition' },
    { key: 'separate', label: 'Notable concurrences / dissents' },
  ],
  pleading: [
    { key: 'court', label: 'Court, parties, cause of action' },
    { key: 'relief', label: 'Relief sought' },
    { key: 'allegations', label: 'Key factual allegations' },
    { key: 'theories', label: 'Legal theories asserted' },
    { key: 'dates', label: 'Deadlines and dates in the document' },
  ],
  other: [
    { key: 'what', label: 'What this document is' },
    { key: 'parties', label: 'Parties or persons named' },
    { key: 'substance', label: 'Substantive content' },
    { key: 'dates', label: 'Dates appearing in the document' },
  ],
}

export const TEMPLATE_NAME: Record<DocType, string> = {
  contract: 'Contract / agreement template',
  opinion: 'Judicial opinion template',
  pleading: 'Pleading / filing template',
  other: 'General document template',
}

/**
 * FR-18: memo as Question Presented → Short Answer → Statement of Facts →
 * Discussion → Conclusion; case brief in its own conventional order. The
 * structure is the point — an attorney should be able to edit the result rather
 * than rewrite it.
 */
export const DRAFT_SECTIONS: Record<DraftType, { key: string; heading: string }[]> = {
  memo: [
    { key: 'qp', heading: 'Question Presented' },
    { key: 'short', heading: 'Short Answer' },
    { key: 'facts', heading: 'Statement of Facts' },
    { key: 'discussion', heading: 'Discussion' },
    { key: 'conclusion', heading: 'Conclusion' },
  ],
  case_brief: [
    { key: 'posture', heading: 'Procedural Posture' },
    { key: 'facts', heading: 'Facts' },
    { key: 'issue', heading: 'Issue' },
    { key: 'holding', heading: 'Holding' },
    { key: 'reasoning', heading: 'Reasoning' },
    { key: 'disposition', heading: 'Disposition' },
  ],
}

export const DRAFT_TYPE_LABEL: Record<DraftType, string> = {
  memo: 'Legal memorandum',
  case_brief: 'Case brief',
}

/* FR-19: the drafting parameters an attorney actually sets. Free text would be
   worse here — these are the values the prompt is built to handle. */

export const AUDIENCE_OPTIONS = [
  { value: 'supervising partner', label: 'Supervising partner' },
  { value: 'client (sophisticated)', label: 'Client — sophisticated' },
  { value: 'client (lay)', label: 'Client — lay reader' },
  { value: 'file memo', label: 'File / own reference' },
  { value: 'opposing counsel', label: 'Opposing counsel' },
]

export const TONE_OPTIONS = [
  { value: 'neutral predictive', label: 'Neutral / predictive' },
  { value: 'advocacy', label: 'Advocacy' },
  { value: 'cautious', label: 'Cautious' },
  { value: 'plain english', label: 'Plain English' },
]

export const POSITION_OPTIONS = [
  { value: '', label: 'No position — assess both sides' },
  { value: 'assignment is permitted without consent', label: 'Assignment is permitted without consent' },
  { value: 'consent is required', label: 'Consent is required' },
  { value: 'the clause is unenforceable', label: 'The clause is unenforceable' },
]
