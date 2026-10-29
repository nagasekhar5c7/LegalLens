import { ScanLine } from 'lucide-react'
import type { OcrInfo } from '@/api/types'
import { cn } from '@/lib/cn'
import { formatPageRanges, pluralise } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Tooltip } from '@/components/ui/Floating'

/**
 * DR-1, and nuance #7.
 *
 * A clause pulled from a mis-OCR'd page is worse than no clause, because it
 * looks correct. The BRD names hostile PDFs as the highest risk in the build,
 * so low-confidence pages are surfaced wherever the document appears rather
 * than averaged into a single score.
 */
export function OcrBadge({ ocr }: { ocr: OcrInfo }) {
  if (!ocr.scanned) return null
  const low = ocr.lowConfidencePages.length

  return (
    <Tooltip
      content={
        low > 0
          ? `Read by OCR at ${Math.round(ocr.meanConfidence * 100)}% mean confidence. ${pluralise(low, 'page')} fell below the floor — anything cited from ${low === 1 ? 'it' : 'them'} is worth checking against the original.`
          : `Read by OCR at ${Math.round(ocr.meanConfidence * 100)}% mean confidence. No page fell below the floor.`
      }
    >
      <Badge tone={low > 0 ? 'caution' : 'neutral'} className="cursor-help">
        <ScanLine aria-hidden className="size-2.5" />
        Scanned
        {low > 0 && <span className="font-semibold">· {low} flagged</span>}
      </Badge>
    </Tooltip>
  )
}

export function OcrWarningPanel({ ocr, className }: { ocr: OcrInfo; className?: string }) {
  if (!ocr.scanned || ocr.lowConfidencePages.length === 0) return null

  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-card border border-caution-line bg-caution-bg px-3 py-2',
        className,
      )}
    >
      <ScanLine aria-hidden className="mt-px size-3.5 shrink-0 text-caution" />
      <div className="min-w-0">
        <p className="text-2xs font-semibold text-caution">
          OCR confidence is low on {pluralise(ocr.lowConfidencePages.length, 'page')}
        </p>
        <p className="mt-0.5 text-2xs leading-relaxed text-caution/90">
          Page{ocr.lowConfidencePages.length === 1 ? '' : 's'}{' '}
          <span className="font-mono">{formatPageRanges(ocr.lowConfidencePages)}</span> were read at
          below the {Math.round(0.5 * 100)}% floor. They are still searchable, but a citation
          landing on them should be verified against the original document.
        </p>
      </div>
    </div>
  )
}
