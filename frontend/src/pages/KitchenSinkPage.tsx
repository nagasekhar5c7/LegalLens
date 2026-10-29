import { Link } from 'react-router-dom'
import { ArrowLeft, Scale, Trash2 } from 'lucide-react'
import type { ApiError, DocStatus, DocType } from '@/api/types'
import { Badge, Chip } from '@/components/ui/Badge'
import { Button, IconButton } from '@/components/ui/Button'
import { EmptyState, Kbd, Progress, Separator, Skeleton, Spinner } from '@/components/ui/Feedback'
import { Checkbox, Field, Input, Select, Switch, Textarea } from '@/components/ui/Field'
import { Popover, PopoverContent, PopoverTrigger, Tooltip } from '@/components/ui/Floating'
import { Dialog, DialogContent, DialogTrigger, SheetContent } from '@/components/ui/Overlay'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from '@/components/ui/Menu'
import { StatusPill } from '@/components/documents/StatusPill'
import { TypeBadge } from '@/components/documents/TypeBadge'
import { OcrWarningPanel } from '@/components/documents/OcrWarning'
import { RefusalCard } from '@/components/chat/RefusalCard'
import { ErrorCard, IncompleteBanner } from '@/components/chat/ErrorCard'
import { CitationChip } from '@/components/chat/CitationChip'
import { Markdown } from '@/components/chat/Markdown'
import { DraftBanner } from '@/components/answer/DraftBanner'
import { RelevanceMeter } from '@/components/evidence/RelevanceMeter'
import { ExcerptCard } from '@/components/evidence/ExcerptCard'
import { buildCitations, buildExcerpts, refusalFor } from '@/dev/fixtures'
import { toast } from '@/store/useToasts'

/**
 * Every primitive and every semantic state, in isolation.
 *
 * Phase 1 has no backend, so this is where states get reviewed without having
 * to provoke them: all four semantic colours side by side, every error
 * variant, every document status. It is also the fastest way to spot a
 * contrast or spacing regression across the set.
 */

const TONES = ['neutral', 'navy', 'grounded', 'refusal', 'draft', 'danger', 'caution'] as const
const STATUSES: DocStatus[] = ['queued', 'parsing', 'ocr', 'indexing', 'ready', 'failed']
const TYPES: DocType[] = ['contract', 'opinion', 'pleading', 'other']

const ERRORS: ApiError[] = [
  {
    kind: 'llm_unavailable',
    message: 'The language model provider is not responding. Retrieval ran and found supporting passages, but no answer could be generated.',
    action: 'Retry, or read the retrieved passages directly — they are listed in the evidence panel.',
    excerptsAvailable: true,
  },
  {
    kind: 'rate_limited',
    message: 'Rate limit reached on the model provider. This request was not charged.',
    action: 'Retry in 24 seconds, or narrow the scope to reduce the request size.',
    retryAfterSec: 24,
  },
  {
    kind: 'parse_failed',
    message: 'Text extraction failed on 7 of 9 pages of “Exhibit Bundle C (fax).pdf”.',
    action: 'Re-upload a 300 dpi scan of the original — OCR confidence averaged 0.31, below the 0.50 floor.',
    documentId: 'doc_exhibits',
  },
  {
    kind: 'timeout',
    message: 'Generation exceeded the 60-second budget and was abandoned. No partial answer is shown, because a truncated legal answer is worse than none.',
    action: 'Retry with a narrower scope, or ask about one document at a time.',
  },
  {
    kind: 'network',
    message: 'The response stream was interrupted. What was written is partial.',
    action: 'Retry to regenerate the full answer.',
  },
]

const SWATCHES: { token: string; cls: string; ink?: string }[] = [
  { token: 'canvas', cls: 'bg-canvas' },
  { token: 'raised', cls: 'bg-raised' },
  { token: 'sunken', cls: 'bg-sunken' },
  { token: 'hover', cls: 'bg-hover' },
  { token: 'active', cls: 'bg-active' },
  { token: 'line', cls: 'bg-line' },
  { token: 'line-strong', cls: 'bg-line-strong' },
  { token: 'navy-600', cls: 'bg-navy-600', ink: 'text-ink-inverse' },
  { token: 'navy-700', cls: 'bg-navy-700', ink: 'text-ink-inverse' },
  { token: 'grounded', cls: 'bg-grounded', ink: 'text-ink-inverse' },
  { token: 'refusal', cls: 'bg-refusal', ink: 'text-ink-inverse' },
  { token: 'draft', cls: 'bg-draft', ink: 'text-ink-inverse' },
  { token: 'danger', cls: 'bg-danger', ink: 'text-ink-inverse' },
  { token: 'caution', cls: 'bg-caution', ink: 'text-ink-inverse' },
  { token: 'highlight', cls: 'bg-highlight' },
  { token: 'highlight-strong', cls: 'bg-highlight-strong' },
]

const SAMPLE_MD = `**Yes — assignment is restricted, with a broad merger carve-out.**

The agreement bars assignment without consent [[1]], subject to a successor carve-out.

1. **Successor carve-out** — assignment permitted without consent on a merger. [[1]]
2. **Restricted Competitor exception** — consent still required. [[2]]

| Route | Notice |
|---|---|
| Convenience | 60 days |
| Non-renewal | 90 days |

> A blockquote, for authority drawn from another document in the set.

Inline \`code\`, *italic*, and **bold** all render.`

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="scroll-mt-16">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {note && <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-2">{note}</p>}
      <div className="mt-3">{children}</div>
      <Separator className="mt-8" />
    </section>
  )
}

export function KitchenSinkPage() {
  const citations = buildCitations(['s_msa_1223', 's_msa_1226'])
  const excerpts = buildExcerpts(['s_msa_1223', 's_msa_1226'])

  return (
    <div className="min-h-dvh bg-canvas">
      <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b border-line bg-canvas/95 px-4 backdrop-blur">
        <span aria-hidden className="grid size-5 place-items-center rounded-[5px] bg-navy-600">
          <Scale className="size-3 text-ink-inverse" />
        </span>
        <h1 className="text-xs font-semibold text-ink">Kitchen sink</h1>
        <Badge tone="neutral">design system · phase 1</Badge>
        <Button size="xs" variant="ghost" className="ml-auto" asChild>
          <Link to="/">
            <ArrowLeft aria-hidden className="size-3.5" />
            Back to the app
          </Link>
        </Button>
      </header>

      <div className="mx-auto max-w-4xl space-y-8 px-4 py-6">
        <Section
          title="Colour"
          note="Colour carries meaning, never decoration: sage for grounded, neutral slate for refusal, amber for an unreviewed draft, brick for errors, ochre for OCR caution."
        >
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SWATCHES.map((s) => (
              <div key={s.token} className="overflow-hidden rounded-card border border-line">
                <div className={`h-12 ${s.cls}`} />
                <p className="bg-raised px-2 py-1 font-mono text-[10px] text-ink-2">{s.token}</p>
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="Type"
          note="Inter for chrome, Source Serif 4 for answers and document text, JetBrains Mono for span ids and trace values."
        >
          <div className="space-y-2 rounded-card border border-line bg-raised p-4">
            <p className="text-lg font-semibold tracking-[-0.015em] text-ink">
              Assignment on a change of control
            </p>
            <p className="text-sm font-semibold text-ink">Section heading, sans 14/600</p>
            <p className="font-serif text-[0.9375rem] leading-[1.68] text-ink">
              Answer body in serif — work product should not look like UI chrome. Neither party may
              assign this Agreement without the prior written consent of the other party.
            </p>
            <p className="text-xs text-ink-2">Secondary copy, 12px, ink-2.</p>
            <p className="text-2xs text-ink-3">Tertiary and meta, 11px, ink-3.</p>
            <p className="font-mono text-[10px] text-ink-3">s_msa_1223 · p. 84 · tr_9fa2c01b</p>
          </div>
        </Section>

        <Section title="Buttons">
          <div className="space-y-2">
            {(['md', 'sm', 'xs'] as const).map((size) => (
              <div key={size} className="flex flex-wrap items-center gap-2">
                {(['primary', 'secondary', 'ghost', 'quiet', 'danger', 'draft'] as const).map((v) => (
                  <Button key={v} size={size} variant={v}>
                    {v}
                  </Button>
                ))}
                <Button size={size} variant="primary" loading>
                  loading
                </Button>
                <Button size={size} variant="primary" disabled>
                  disabled
                </Button>
              </div>
            ))}
            <div className="flex items-center gap-2 pt-1">
              <IconButton label="Delete" variant="ghost">
                <Trash2 className="size-4" />
              </IconButton>
              <IconButton label="Delete" variant="danger">
                <Trash2 className="size-4" />
              </IconButton>
              <Kbd>⌘K</Kbd>
              <Kbd>⌥↓</Kbd>
              <Spinner className="size-4 text-navy-600" />
            </div>
          </div>
        </Section>

        <Section title="Badges and chips">
          <div className="flex flex-wrap items-center gap-1.5">
            {TONES.map((t) => (
              <Badge key={t} tone={t}>
                {t}
              </Badge>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {TONES.map((t) => (
              <Chip key={t} tone={t} selected>
                {t} selected
              </Chip>
            ))}
            <Chip>unselected</Chip>
            <Chip disabled>disabled</Chip>
          </div>
        </Section>

        <Section
          title="Document status"
          note="Only “ready” is searchable (FR-7), so only it gets the grounded colour. Everything else reads as not-yet-usable."
        >
          <div className="flex flex-wrap items-center gap-2">
            {STATUSES.map((s) => (
              <StatusPill key={s} status={s} progress={62} />
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {TYPES.map((t) => (
              <TypeBadge key={t} type={t} source="detected" confidence={0.95} />
            ))}
            <TypeBadge type="other" source="detected" confidence={0.63} />
            <TypeBadge type="contract" source="overridden" confidence={0.71} />
          </div>
          <OcrWarningPanel
            className="mt-3"
            ocr={{ scanned: true, meanConfidence: 0.79, lowConfidencePages: [3, 4] }}
          />
        </Section>

        <Section title="Form controls">
          <div className="grid gap-4 rounded-card border border-line bg-raised p-4 sm:grid-cols-2">
            <Field label="Question presented" required hint="Becomes the memo's first section.">
              {(p) => <Input {...p} placeholder="Whether Nexora may withhold consent…" />}
            </Field>
            <Field label="Email" error="Enter a valid email address.">
              {(p) => <Input {...p} defaultValue="not-an-email" />}
            </Field>
            <Field label="Audience">
              {(p) => (
                <Select
                  {...p}
                  options={[
                    { value: 'partner', label: 'Supervising partner' },
                    { value: 'client', label: 'Client — sophisticated' },
                  ]}
                />
              )}
            </Field>
            <Field label="Disabled">{(p) => <Input {...p} disabled value="Not editable" />}</Field>
            <Field label="Notes" className="sm:col-span-2">
              {(p) => <Textarea {...p} rows={3} placeholder="Anything else worth recording…" />}
            </Field>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-xs text-ink">
                <Checkbox defaultChecked /> Checked
              </label>
              <label className="flex items-center gap-2 text-xs text-ink">
                <Checkbox /> Unchecked
              </label>
              <label className="flex items-center gap-2 text-xs text-ink">
                <Checkbox indeterminate /> Mixed
              </label>
            </div>
            <Switch label="Stream responses" description="Token-by-token over SSE." defaultChecked />
          </div>
        </Section>

        <Section title="Overlays and menus">
          <div className="flex flex-wrap items-center gap-2">
            <Tooltip content="A short label, never the only place information lives">
              <Button size="sm">Tooltip</Button>
            </Tooltip>

            <Popover>
              <PopoverTrigger asChild>
                <Button size="sm">Popover</Button>
              </PopoverTrigger>
              <PopoverContent className="w-64 p-3">
                <p className="text-xs text-ink">Popover content, scrollable and collision-aware.</p>
              </PopoverContent>
            </Popover>

            <Menu>
              <MenuTrigger asChild>
                <Button size="sm">Menu</Button>
              </MenuTrigger>
              <MenuContent>
                <MenuLabel>Group</MenuLabel>
                <MenuItem shortcut="⌘C">Copy</MenuItem>
                <MenuItem>Regenerate</MenuItem>
                <MenuSeparator />
                <MenuItem tone="danger">
                  <Trash2 aria-hidden className="size-3.5" />
                  Delete
                </MenuItem>
              </MenuContent>
            </Menu>

            <Dialog>
              <DialogTrigger asChild>
                <Button size="sm">Dialog</Button>
              </DialogTrigger>
              <DialogContent
                title="A centred modal"
                description="Focus trap, restore and Esc come from Radix — which is why we are not hand-rolling it."
                size="sm"
                footer={<Button size="sm" variant="primary">Confirm</Button>}
              >
                <p className="px-5 py-4 text-xs text-ink-2">Body content.</p>
              </DialogContent>
            </Dialog>

            <Dialog>
              <DialogTrigger asChild>
                <Button size="sm">Sheet</Button>
              </DialogTrigger>
              <SheetContent title="Edge-docked panel" description="Used for draft settings and traces.">
                <p className="p-4 text-xs text-ink-2">Body content.</p>
              </SheetContent>
            </Dialog>

            <Button size="sm" variant="secondary" onClick={() => toast.done('Saved', 'A confirmation toast.')}>
              Toast · done
            </Button>
            <Button size="sm" variant="secondary" onClick={() => toast.warn('Heads up', 'A caution toast.')}>
              Toast · warn
            </Button>
            <Button size="sm" variant="secondary" onClick={() => toast.error('Failed', 'An error toast.')}>
              Toast · error
            </Button>
          </div>
        </Section>

        <Section title="Loading and empty">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2 rounded-card border border-line bg-raised p-3">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-4/5" />
              <Progress value={62} />
              <Progress value={88} tone="grounded" />
              <Progress value={40} tone="caution" />
            </div>
            <div className="rounded-card border border-line bg-raised">
              <EmptyState
                title="Nothing to verify yet"
                body="Retrieved passages appear here as an answer streams."
                action={<Button size="sm" variant="secondary">Upload documents</Button>}
              />
            </div>
          </div>
        </Section>

        <Section
          title="Answer rendering"
          note="Citation chips are interactive React elements inside the markdown, not a string replace — hover for the excerpt, click to open the evidence panel."
        >
          <div className="rounded-card border border-line bg-raised p-4">
            <Markdown md={SAMPLE_MD} citations={citations} excerpts={excerpts} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {citations.map((c) => (
              <CitationChip key={c.n} citation={c} excerpt={excerpts.find((e) => e.spanId === c.spanId)} />
            ))}
            <RelevanceMeter value={0.94} />
            <RelevanceMeter value={0.62} />
            <RelevanceMeter value={0.31} />
          </div>
          <div className="mt-3 max-w-sm">
            <ExcerptCard excerpt={excerpts[0]} citation={citations[0]} active onSelect={() => {}} />
          </div>
        </Section>

        <Section
          title="Refusal — a success state"
          note="The single most consequential styling decision here. A correct refusal is a measured success at ≥90%, so it gets calm slate, never red, and keeps its feedback bar."
        >
          <RefusalCard
            refusal={refusalFor(
              'What does the MSA say about force majeure and pandemic relief?',
              'your workspace (6 documents, 414 pages)',
            )}
          />
        </Section>

        <Section
          title="Errors — each names a next action"
          note="Note the first: when the provider is down, retrieval has already run, so the card offers the retrieved passages instead of becoming a dead end (NFR-6)."
        >
          <div className="space-y-3">
            {ERRORS.map((e) => (
              <ErrorCard key={e.kind} error={e} onRetry={() => {}} onShowExcerpts={() => {}} />
            ))}
            <IncompleteBanner />
          </div>
        </Section>

        <Section
          title="Draft marking"
          note="Persistent on screen, injected into every copy and export, and printed diagonally across the page."
        >
          <div className="overflow-hidden rounded-card border border-draft-line bg-raised">
            <DraftBanner draftType="memo" />
            <p className="p-4 font-serif text-[0.9375rem] leading-[1.68] text-ink">
              Probably not as to consent, but the transaction does expose the agreement to
              termination.
            </p>
          </div>
        </Section>
      </div>
    </div>
  )
}
