/**
 * Forceable states. Every non-happy path in the UI is reachable from here.
 *
 * These are no longer simulated in the browser: the id travels to the backend
 * as `X-LegalLens-Scenario` and its fixture engine takes that path for real, so
 * what the UI renders is a genuine response. Mirrors
 * `backend/app/ai/scenarios.py`, which is also where the dev-only guard lives —
 * the header is ignored outright when `LL_ENV=production`, and `/api/config`
 * reports `scenariosEnabled: false` so the menu hides itself.
 */
export type Scenario =
  | 'auto'
  | 'refusal'
  | 'llm_down'
  | 'rate_limited'
  | 'timeout'
  | 'interrupted'
  | 'slow'

export const SCENARIOS: {
  id: Scenario
  label: string
  description: string
  tone: 'neutral' | 'refusal' | 'danger' | 'caution'
}[] = [
  {
    id: 'auto',
    label: 'Follow the question',
    description: 'Answers what the corpus supports and refuses what it does not. The honest default.',
    tone: 'neutral',
  },
  {
    id: 'refusal',
    label: 'Always refuse',
    description: 'Forces the "not in your documents" outcome — a success state, not an error (FR-14).',
    tone: 'refusal',
  },
  {
    id: 'llm_down',
    label: 'LLM provider down',
    description: 'Generation fails, retrieval survives. Excerpts remain viewable (NFR-6).',
    tone: 'danger',
  },
  {
    id: 'rate_limited',
    label: 'Rate limited',
    description: 'Provider returns 429 with a retry window.',
    tone: 'caution',
  },
  {
    id: 'timeout',
    label: 'Generation timeout',
    description: 'Stream stalls past the budget and is abandoned with a retry path.',
    tone: 'caution',
  },
  {
    id: 'interrupted',
    label: 'Stream interrupted',
    description: 'Cuts off mid-answer. The partial output must be marked incomplete (NFR-7).',
    tone: 'caution',
  },
  {
    id: 'slow',
    label: 'Slow generation',
    description: 'Four-times latency, for checking that the stage trace carries a long wait.',
    tone: 'neutral',
  },
]
