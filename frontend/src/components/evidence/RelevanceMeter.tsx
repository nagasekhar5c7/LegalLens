import { cn } from '@/lib/cn'
import { Tooltip } from '@/components/ui/Floating'

/**
 * Reranker score. Three bars, not a number, because the absolute value is
 * meaningless to the reader — what matters is "strong / adequate / weak, and
 * above the floor at all".
 */
export function RelevanceMeter({ value, className }: { value: number; className?: string }) {
  const bars = value >= 0.8 ? 3 : value >= 0.55 ? 2 : 1
  const tone = bars === 3 ? 'bg-grounded' : bars === 2 ? 'bg-navy-500' : 'bg-caution'
  const word = bars === 3 ? 'Strong' : bars === 2 ? 'Adequate' : 'Weak'

  return (
    <Tooltip content={`${word} match — reranker score ${value.toFixed(2)} (floor 0.25)`}>
      <span
        className={cn('flex h-3.5 shrink-0 items-end gap-px', className)}
        aria-label={`Relevance ${word.toLowerCase()}, ${value.toFixed(2)}`}
      >
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className={cn(
              'w-1 rounded-[1px]',
              i === 0 ? 'h-1.5' : i === 1 ? 'h-2.5' : 'h-3.5',
              i < bars ? tone : 'bg-active',
            )}
          />
        ))}
      </span>
    </Tooltip>
  )
}
