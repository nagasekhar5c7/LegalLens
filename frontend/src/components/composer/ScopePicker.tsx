import { useState } from 'react'
import { Check, ChevronDown, FileText, Layers, Library } from 'lucide-react'
import type { LegalDocument, Scope } from '@/api/types'
import { cn } from '@/lib/cn'
import { DOC_STATUS_LABEL, shortDocName, statusExclusionReason } from '@/lib/format'
import { Button } from '@/components/ui/Button'
import { Checkbox } from '@/components/ui/Field'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Floating'
import { StatusPill } from '@/components/documents/StatusPill'

/**
 * FR-8 / FR-9, and nuance #7.
 *
 * A document that is not `ready` is shown, disabled, *with the reason*.
 * Silently omitting it would produce an answer the user cannot explain.
 */
export function ScopePicker({
  documents,
  scope,
  onChange,
}: {
  documents: LegalDocument[]
  scope: Scope
  onChange: (s: Scope) => void
}) {
  const [open, setOpen] = useState(false)
  const ready = documents.filter((d) => d.status === 'ready')
  const notReady = documents.filter((d) => d.status !== 'ready')

  const label =
    scope.kind === 'workspace'
      ? `Whole workspace · ${ready.length} docs`
      : scope.documentIds.length === 1
        ? shortDocName(documents.find((d) => d.id === scope.documentIds[0])?.name ?? '', 26)
        : `${scope.documentIds.length} documents`

  function toggle(id: string) {
    const next = scope.documentIds.includes(id)
      ? scope.documentIds.filter((x) => x !== id)
      : [...scope.documentIds, id]
    onChange(
      next.length === 0
        ? { kind: 'workspace', documentIds: [] }
        : { kind: next.length === 1 ? 'document' : 'subset', documentIds: next },
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="xs" variant="ghost" className="max-w-56 gap-1.5">
          {scope.kind === 'workspace' ? (
            <Library aria-hidden className="size-3.5 shrink-0" />
          ) : (
            <Layers aria-hidden className="size-3.5 shrink-0" />
          )}
          <span className="truncate">{label}</span>
          <ChevronDown aria-hidden className="size-3 shrink-0 opacity-60" />
        </Button>
      </PopoverTrigger>

      <PopoverContent align="start" side="top" className="w-[22rem]">
        <div className="border-b border-line p-1">
          <button
            type="button"
            onClick={() => {
              onChange({ kind: 'workspace', documentIds: [] })
              setOpen(false)
            }}
            className={cn(
              'flex w-full items-start gap-2 rounded-[5px] px-2 py-2 text-left hover:bg-hover',
              scope.kind === 'workspace' && 'bg-navy-50',
            )}
          >
            <Library aria-hidden className="mt-px size-3.5 shrink-0 text-navy-600" />
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-medium text-ink">Whole workspace</span>
              <span className="block text-2xs text-ink-3">
                {ready.length} ready · {ready.reduce((n, d) => n + d.pages, 0).toLocaleString()} pages
                {notReady.length > 0 && ` · ${notReady.length} excluded`}
              </span>
            </span>
            {scope.kind === 'workspace' && (
              <Check aria-hidden className="mt-0.5 size-3.5 shrink-0 text-navy-600" />
            )}
          </button>
        </div>

        <fieldset className="p-1">
          <legend className="px-2 pb-1 pt-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-3">
            Or pick documents
          </legend>

          {ready.map((d) => (
            <label
              key={d.id}
              className="flex cursor-pointer items-start gap-2 rounded-[5px] px-2 py-1.5 hover:bg-hover"
            >
              <Checkbox
                checked={scope.kind !== 'workspace' && scope.documentIds.includes(d.id)}
                onCheckedChange={() => toggle(d.id)}
                className="mt-0.5"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-ink">{shortDocName(d.name, 38)}</span>
                <span className="block text-2xs text-ink-3">
                  {d.pages} pages · {d.type}
                </span>
              </span>
            </label>
          ))}

          {notReady.length > 0 && (
            <div className="mt-1 border-t border-line pt-1">
              {notReady.map((d) => (
                <div
                  key={d.id}
                  className="flex items-start gap-2 px-2 py-1.5 opacity-70"
                  title={statusExclusionReason(d.status) ?? ''}
                >
                  <FileText aria-hidden className="mt-0.5 size-3.5 shrink-0 text-ink-3" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs text-ink-3 line-through decoration-ink-3/40">
                      {shortDocName(d.name, 34)}
                    </span>
                    <span className="block text-2xs text-caution">
                      {statusExclusionReason(d.status) ?? DOC_STATUS_LABEL[d.status]}
                    </span>
                  </span>
                  <StatusPill status={d.status} progress={d.progress} compact />
                </div>
              ))}
            </div>
          )}
        </fieldset>
      </PopoverContent>
    </Popover>
  )
}
