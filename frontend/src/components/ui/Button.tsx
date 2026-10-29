import { forwardRef } from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cn } from '@/lib/cn'
import { Spinner } from './Feedback'

type Variant = 'primary' | 'secondary' | 'ghost' | 'quiet' | 'danger' | 'draft'
type Size = 'xs' | 'sm' | 'md'

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-navy-600 text-ink-inverse hover:bg-navy-700 active:bg-navy-900 shadow-sm disabled:bg-navy-300',
  secondary:
    'bg-raised text-ink border border-line-strong hover:bg-hover active:bg-active shadow-sm',
  ghost: 'text-ink-2 hover:bg-hover hover:text-ink active:bg-active',
  quiet: 'text-ink-3 hover:text-ink hover:bg-hover',
  danger: 'bg-danger text-ink-inverse hover:brightness-110 active:brightness-95 shadow-sm',
  draft: 'bg-draft-bg text-draft border border-draft-line hover:brightness-[0.98]',
}

const SIZE: Record<Size, string> = {
  xs: 'h-6 gap-1 px-2 text-2xs rounded-[5px]',
  sm: 'h-7.5 gap-1.5 px-2.5 text-xs rounded-control',
  md: 'h-9 gap-2 px-3.5 text-sm rounded-control',
}

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  /** Render as the single child element instead of a <button> (e.g. a Link). */
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'sm', loading, asChild, children, disabled, ...rest },
  ref,
) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-[background-color,color,box-shadow,opacity] duration-100',
        'disabled:pointer-events-none disabled:opacity-55',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <>
          <Spinner className="size-3.5" />
          {children}
        </>
      ) : (
        children
      )}
    </Comp>
  )
})

export interface IconButtonProps extends ButtonProps {
  /** Required: icon-only controls must still be named for screen readers. */
  label: string
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ label, className, size = 'sm', variant = 'ghost', ...rest }, ref) {
    return (
      <Button
        ref={ref}
        aria-label={label}
        variant={variant}
        size={size}
        className={cn('px-0', size === 'xs' ? 'w-6' : size === 'sm' ? 'w-7.5' : 'w-9', className)}
        {...rest}
      />
    )
  },
)
