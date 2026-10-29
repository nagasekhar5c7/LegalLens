import { AlertCircle, Check, Clock, Eye, FileSearch, Loader } from 'lucide-react'
import type { DocStatus } from '@/api/types'
import { cn } from '@/lib/cn'
import { DOC_STATUS_LABEL } from '@/lib/format'
import { Tooltip } from '@/components/ui/Floating'

/**
 * FR-3 lifecycle: queued → parsing → (ocr) → indexing → ready | failed.
 * `ready` is the only status that enters retrieval (FR-7), so it is the only
 * one in the grounded colour — everything else reads as "not yet usable".
 */
const STYLE: Record<DocStatus, { cls: string; icon: React.ReactNode; help: string }> = {
  queued: {
    cls: 'bg-sunken text-ink-3 border-line',
    icon: <Clock aria-hidden className="size-3" />,
    help: 'Waiting for an ingestion slot. Not searchable yet.',
  },
  parsing: {
    cls: 'bg-navy-50 text-navy-700 border-navy-200',
    icon: <FileSearch aria-hidden className="size-3" />,
    help: 'Extracting text with page and position metadata. Not searchable yet.',
  },
  ocr: {
    cls: 'bg-caution-bg text-caution border-caution-line',
    icon: <Eye aria-hidden className="size-3" />,
    help: 'No text layer found, so the pages are being read by OCR. Not searchable yet.',
  },
  indexing: {
    cls: 'bg-navy-50 text-navy-700 border-navy-200',
    icon: <Loader aria-hidden className="size-3" />,
    help: 'Chunking and embedding. Not searchable until this finishes.',
  },
  ready: {
    cls: 'bg-grounded-bg text-grounded border-grounded-line',
    icon: <Check aria-hidden className="size-3" />,
    help: 'Indexed and searchable.',
  },
  failed: {
    cls: 'bg-danger-bg text-danger border-danger-line',
    icon: <AlertCircle aria-hidden className="size-3" />,
    help: 'Could not be read. Excluded from every search rather than indexed with unreliable text.',
  },
}

export function StatusPill({
  status,
  progress,
  compact,
  className,
}: {
  status: DocStatus
  progress?: number
  compact?: boolean
  className?: string
}) {
  const s = STYLE[status]
  const spinning = status === 'parsing' || status === 'indexing' || status === 'ocr'

  return (
    <Tooltip content={s.help}>
      <span
        className={cn(
          'inline-flex h-5 shrink-0 cursor-help items-center gap-1 rounded-full border px-1.5 text-2xs font-medium',
          s.cls,
          className,
        )}
      >
        <span className={cn(spinning && 'animate-[ll-spin_1.6s_linear_infinite]')}>{s.icon}</span>
        {!compact && (
          <>
            {DOC_STATUS_LABEL[status]}
            {spinning && progress !== undefined && (
              <span className="font-mono tabular-nums opacity-70">{Math.round(progress)}%</span>
            )}
          </>
        )}
      </span>
    </Tooltip>
  )
}
