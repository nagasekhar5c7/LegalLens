import { useState } from 'react'
import { FileSignature, Scale, SlidersHorizontal } from 'lucide-react'
import type { DraftParams, DraftType, LegalDocument } from '@/api/types'
import { cn } from '@/lib/cn'
import { shortDocName } from '@/lib/format'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { Checkbox, Field, Select, Textarea } from '@/components/ui/Field'
import { Dialog, DialogTrigger, SheetContent } from '@/components/ui/Overlay'
import { AUDIENCE_OPTIONS, POSITION_OPTIONS, TONE_OPTIONS } from '@/lib/templates'

const TYPES: { id: DraftType; label: string; icon: React.ReactNode; structure: string }[] = [
  {
    id: 'memo',
    label: 'Legal memorandum',
    icon: <FileSignature aria-hidden className="size-4" />,
    structure: 'Question Presented → Short Answer → Statement of Facts → Discussion → Conclusion',
  },
  {
    id: 'case_brief',
    label: 'Case brief',
    icon: <Scale aria-hidden className="size-4" />,
    structure: 'Posture → Facts → Issue → Holding → Reasoning → Disposition',
  },
]

/** FR-19: the parameters a draft needs before it is worth generating. */
export function DraftParamsDrawer({
  params,
  documents,
  onChange,
  onGenerate,
}: {
  params: DraftParams
  documents: LegalDocument[]
  onChange: (p: Partial<DraftParams>) => void
  onGenerate: () => void
}) {
  const [open, setOpen] = useState(false)
  const ready = documents.filter((d) => d.status === 'ready')
  const chosen = params.documentIds.length
  const incomplete = chosen === 0 || !params.questionPresented.trim()

  function toggleDoc(id: string) {
    onChange({
      documentIds: params.documentIds.includes(id)
        ? params.documentIds.filter((x) => x !== id)
        : [...params.documentIds, id],
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="xs" variant={incomplete ? 'secondary' : 'ghost'} className="gap-1.5">
          <SlidersHorizontal aria-hidden className="size-3.5" />
          Draft settings
          {incomplete ? (
            <Badge tone="caution" className="h-4 px-1.5">
              needed
            </Badge>
          ) : (
            <Badge tone="neutral" className="h-4 px-1.5">
              {chosen} source{chosen === 1 ? '' : 's'}
            </Badge>
          )}
        </Button>
      </DialogTrigger>

      <SheetContent
        title="Draft settings"
        description="A draft is only as good as its parameters. These are passed through to the draft and shown on the output."
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Close
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={incomplete}
              onClick={() => {
                setOpen(false)
                onGenerate()
              }}
            >
              Generate draft
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-5 p-4">
          <fieldset>
            <legend className="text-xs font-medium text-ink">Draft type</legend>
            <div className="mt-2 grid gap-2">
              {TYPES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onChange({ draftType: t.id })}
                  aria-pressed={params.draftType === t.id}
                  className={cn(
                    'flex items-start gap-2.5 rounded-card border p-2.5 text-left transition-colors',
                    params.draftType === t.id
                      ? 'border-navy-600 bg-navy-50'
                      : 'border-line bg-raised hover:bg-hover',
                  )}
                >
                  <span className="mt-px shrink-0 text-navy-600">{t.icon}</span>
                  <span className="min-w-0">
                    <span className="block text-xs font-semibold text-ink">{t.label}</span>
                    <span className="mt-0.5 block text-2xs leading-relaxed text-ink-3">
                      {t.structure}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          </fieldset>

          <Field
            label="Question presented"
            required
            hint="The question the draft must answer. This becomes the first section of a memo verbatim."
          >
            {(p) => (
              <Textarea
                {...p}
                rows={3}
                value={params.questionPresented}
                onChange={(e) => onChange({ questionPresented: e.target.value })}
                placeholder="Whether Nexora may withhold consent to assignment where Arclight transfers more than fifty percent of its voting securities…"
              />
            )}
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Audience" hint="Sets register and how much is explained.">
              {(p) => (
                <Select
                  {...p}
                  options={AUDIENCE_OPTIONS}
                  value={params.audience}
                  onChange={(e) => onChange({ audience: e.target.value })}
                />
              )}
            </Field>
            <Field label="Tone">
              {(p) => (
                <Select
                  {...p}
                  options={TONE_OPTIONS}
                  value={params.tone}
                  onChange={(e) => onChange({ tone: e.target.value })}
                />
              )}
            </Field>
          </div>

          <Field
            label="Position to argue"
            hint="Leave unset for a neutral predictive memo. A position makes the draft advocate it — and the contrary authority is still surfaced."
          >
            {(p) => (
              <Select
                {...p}
                options={POSITION_OPTIONS}
                value={params.position}
                onChange={(e) => onChange({ position: e.target.value })}
              />
            )}
          </Field>

          <fieldset>
            <legend className="flex items-center gap-2 text-xs font-medium text-ink">
              Source documents
              <span className="text-2xs font-normal text-ink-3">
                {chosen === 0 ? 'at least one required' : `${chosen} selected`}
              </span>
            </legend>
            <p className="mt-1 text-2xs leading-relaxed text-ink-3">
              The draft may cite nothing outside these documents — no case, statute or docket number
              the model happens to know.
            </p>
            <div className="mt-2 divide-y divide-line rounded-card border border-line">
              {ready.map((d) => (
                <label
                  key={d.id}
                  className="flex cursor-pointer items-start gap-2.5 px-2.5 py-2 hover:bg-hover"
                >
                  <Checkbox
                    checked={params.documentIds.includes(d.id)}
                    onCheckedChange={() => toggleDoc(d.id)}
                    className="mt-0.5"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs text-ink">{shortDocName(d.name, 40)}</span>
                    <span className="block text-2xs text-ink-3">
                      {d.type} · {d.pages} pages
                    </span>
                  </span>
                </label>
              ))}
              {ready.length === 0 && (
                <p className="px-2.5 py-3 text-2xs text-ink-3">
                  No documents are ready. Upload one first.
                </p>
              )}
            </div>
          </fieldset>
        </div>
      </SheetContent>
    </Dialog>
  )
}
