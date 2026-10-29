import { AlertCircle, Check, Clock, Loader, MessageSquare } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { IngestionStep, LegalDocument } from '@/api/types'
import { cn } from '@/lib/cn'
import { absoluteTime, formatBytes, formatMs } from '@/lib/format'
import { Button } from '@/components/ui/Button'
import { Dialog, SheetContent } from '@/components/ui/Overlay'
import { StatusPill } from './StatusPill'
import { TypeBadge } from './TypeBadge'
import { OcrWarningPanel } from './OcrWarning'

const STEP_ICON: Record<IngestionStep['status'], React.ReactNode> = {
  done: <Check aria-hidden className="size-3 text-grounded" />,
  active: <Loader aria-hidden className="size-3 animate-[ll-spin_1.4s_linear_infinite] text-navy-600" />,
  pending: <Clock aria-hidden className="size-3 text-ink-3" />,
  failed: <AlertCircle aria-hidden className="size-3 text-danger" />,
}

/** The ingestion log, because "failed" without a reason is not actionable. */
export function DocumentDetailDrawer({
  document: doc,
  open,
  onOpenChange,
}: {
  document: LegalDocument | null
  open: boolean
  onOpenChange: (o: boolean) => void
}) {
  const navigate = useNavigate()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {doc && (
        <SheetContent
          title={doc.name}
          description={`${doc.pages} pages · ${formatBytes(doc.sizeBytes)} · added ${absoluteTime(doc.uploadedAt)}`}
          footer={
            <>
              <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={doc.status !== 'ready'}
                onClick={() => {
                  onOpenChange(false)
                  navigate(`/doc/${doc.id}`)
                }}
              >
                Open the document
              </Button>
            </>
          }
        >
          <div className="space-y-5 p-4">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusPill status={doc.status} progress={doc.progress} />
              <TypeBadge
                type={doc.type}
                source={doc.typeSource}
                confidence={doc.classifierConfidence}
              />
            </div>

            {doc.ocr && <OcrWarningPanel ocr={doc.ocr} />}

            {doc.failure && (
              <section className="rounded-card border border-danger-line bg-danger-bg px-3 py-2.5">
                <h3 className="text-2xs font-semibold text-danger">{doc.failure.reason}</h3>
                <p className="mt-1 text-2xs leading-relaxed text-ink-2">{doc.failure.detail}</p>
                <p className="mt-2 text-2xs font-medium text-ink">
                  This document is excluded from every search. A failed parse is never indexed with
                  unreliable text.
                </p>
              </section>
            )}

            <section>
              <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                Ingestion log
              </h3>
              <ol className="mt-2 space-y-px">
                {(doc.ingestion ?? []).map((step, i) => (
                  <li
                    key={`${step.label}-${i}`}
                    className={cn(
                      'flex items-baseline gap-2 rounded-control px-2.5 py-1.5',
                      step.status === 'failed' ? 'bg-danger-bg' : 'bg-canvas',
                    )}
                  >
                    <span className="mt-px shrink-0">{STEP_ICON[step.status]}</span>
                    <span
                      className={cn(
                        'shrink-0 text-xs',
                        step.status === 'pending' ? 'text-ink-3' : 'font-medium text-ink',
                      )}
                    >
                      {step.label}
                    </span>
                    {step.detail && (
                      <span className="min-w-0 truncate text-2xs text-ink-2">{step.detail}</span>
                    )}
                    {step.ms !== undefined && (
                      <span className="ml-auto shrink-0 font-mono text-[10px] text-ink-3">
                        {formatMs(step.ms)}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </section>

            {doc.status === 'ready' && (
              <Button
                size="sm"
                variant="secondary"
                className="w-full"
                onClick={() => {
                  onOpenChange(false)
                  navigate('/')
                }}
              >
                <MessageSquare aria-hidden className="size-3.5" />
                Ask about this document
              </Button>
            )}
          </div>
        </SheetContent>
      )}
    </Dialog>
  )
}
