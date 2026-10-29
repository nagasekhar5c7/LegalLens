import { cn } from '@/lib/cn'

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="presentation"
      className={cn(
        'inline-block size-4 shrink-0 rounded-full border-2 border-current border-t-transparent opacity-70',
        className,
      )}
      style={{ animation: 'll-spin 700ms linear infinite' }}
    />
  )
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn('rounded-control bg-sunken', className)}
      style={{
        background:
          'linear-gradient(90deg, var(--color-sunken) 25%, var(--color-hover) 37%, var(--color-sunken) 63%)',
        backgroundSize: '400% 100%',
        animation: 'll-shimmer 1.4s ease-in-out infinite',
      }}
    />
  )
}

export function Progress({
  value,
  tone = 'navy',
  className,
  label,
}: {
  value: number
  tone?: 'navy' | 'grounded' | 'caution' | 'danger'
  className?: string
  label?: string
}) {
  const bar = {
    navy: 'bg-navy-600',
    grounded: 'bg-grounded',
    caution: 'bg-caution',
    danger: 'bg-danger',
  }[tone]
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(value)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn('h-1 w-full overflow-hidden rounded-full bg-active', className)}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-300 ease-out', bar)}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  )
}

export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded-[4px] border border-line',
        'bg-raised px-1 font-sans text-2xs font-medium text-ink-3 shadow-[0_1px_0_var(--color-line)]',
        className,
      )}
    >
      {children}
    </kbd>
  )
}

export function Separator({
  orientation = 'horizontal',
  className,
}: {
  orientation?: 'horizontal' | 'vertical'
  className?: string
}) {
  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        'shrink-0 bg-line',
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        className,
      )}
    />
  )
}

export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
  tone = 'neutral',
}: {
  icon?: React.ReactNode
  title: string
  body?: React.ReactNode
  action?: React.ReactNode
  className?: string
  tone?: 'neutral' | 'quiet'
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 py-10 text-center',
        tone === 'quiet' && 'py-6',
        className,
      )}
    >
      {icon && (
        <div className="mb-3 grid size-10 place-items-center rounded-full bg-sunken text-ink-3">
          {icon}
        </div>
      )}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {body && <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-ink-2">{body}</p>}
      {action && <div className="mt-4 flex items-center gap-2">{action}</div>}
    </div>
  )
}
