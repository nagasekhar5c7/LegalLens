import { forwardRef } from 'react'
import { cn } from '@/lib/cn'

type Tone =
  | 'neutral'
  | 'navy'
  | 'grounded'
  | 'refusal'
  | 'draft'
  | 'danger'
  | 'caution'

/** Semantic tones. Colour means something here — see theme.css. */
const TONE: Record<Tone, string> = {
  neutral: 'bg-sunken text-ink-2 border-line',
  navy: 'bg-navy-50 text-navy-700 border-navy-200',
  grounded: 'bg-grounded-bg text-grounded border-grounded-line',
  refusal: 'bg-refusal-bg text-refusal border-refusal-line',
  draft: 'bg-draft-bg text-draft border-draft-line',
  danger: 'bg-danger-bg text-danger border-danger-line',
  caution: 'bg-caution-bg text-caution border-caution-line',
}

export const Badge = forwardRef<
  HTMLSpanElement,
  React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; mono?: boolean }
>(function Badge({ tone = 'neutral', mono, className, ...rest }, ref) {
  return (
    <span
      ref={ref}
      className={cn(
        'inline-flex h-5 shrink-0 items-center gap-1 rounded-full border px-2',
        'text-2xs font-medium whitespace-nowrap',
        mono && 'font-mono tracking-tight',
        TONE[tone],
        className,
      )}
      {...rest}
    />
  )
})

/** A selectable chip: mode selection, filters, feedback reasons. */
export const Chip = forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & { selected?: boolean; tone?: Tone }
>(function Chip({ selected, tone = 'navy', className, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      aria-pressed={selected}
      className={cn(
        'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5',
        'text-xs font-medium transition-colors duration-100',
        'disabled:pointer-events-none disabled:opacity-50',
        selected
          ? cn(TONE[tone], 'shadow-sm')
          : 'border-line bg-raised text-ink-2 hover:bg-hover hover:text-ink',
        className,
      )}
      {...rest}
    />
  )
})
