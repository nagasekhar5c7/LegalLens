import { FileSignature, ShieldAlert } from 'lucide-react'

/**
 * FR-23 / TS-5, and nuance #3.
 *
 * Persistent, not dismissible, and deliberately the loudest thing on screen
 * for a draft. The same text is injected into every copy and export by
 * `lib/exporting.ts`, and onto paper by `PrintWatermark` below — a banner the
 * user can copy or print out of is theatre.
 */
export function DraftBanner({ draftType }: { draftType: 'memo' | 'case_brief' }) {
  return (
    <>
      <PrintWatermark />
      <div className="sticky top-0 z-10 -mx-px flex items-start gap-2.5 rounded-t-card border-b border-draft-line bg-draft-bg px-3.5 py-2.5 backdrop-blur-sm">
        <ShieldAlert aria-hidden className="mt-px size-4 shrink-0 text-draft" />
        <div className="min-w-0 flex-1">
          <p className="text-2xs font-bold uppercase tracking-[0.08em] text-draft">
            Unreviewed AI draft — attorney review required
          </p>
          <p className="mt-0.5 text-2xs leading-relaxed text-draft/85">
            Generated from your own documents. Not legal advice. You remain responsible for this{' '}
            {draftType === 'memo' ? 'memorandum' : 'brief'} and must verify every citation before
            use. This notice is carried into every copy, export and printout.
          </p>
        </div>
        <FileSignature aria-hidden className="mt-px size-4 shrink-0 text-draft/50" />
      </div>
    </>
  )
}

/**
 * A printed draft left on a desk is the artefact most likely to be mistaken
 * for finished work product, so the marking goes across the page itself.
 */
function PrintWatermark() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-50 hidden place-items-center print:grid"
    >
      <p className="-rotate-[32deg] text-center text-5xl font-bold uppercase tracking-[0.2em] text-draft/15">
        Unreviewed
        <br />
        AI draft
      </p>
    </div>
  )
}

/** TS-5 on non-draft output: quieter, but always present. */
export function WorkProductNote() {
  return (
    <p className="mt-4 border-t border-line pt-2.5 text-[10px] leading-relaxed text-ink-3">
      Attorney work-product aid, not legal advice. Every claim above is cited to your documents —
      verify each citation against its source before relying on it.
    </p>
  )
}
