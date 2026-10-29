import { forwardRef, useId } from 'react'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import * as CheckboxPrimitive from '@radix-ui/react-checkbox'
import { Check, ChevronDown, Minus } from 'lucide-react'
import { cn } from '@/lib/cn'

const control =
  'w-full rounded-control border border-line-strong bg-raised px-2.5 text-sm text-ink ' +
  'placeholder:text-ink-3 transition-[border-color,box-shadow] duration-100 ' +
  'hover:border-ink-3 focus:border-navy-600 focus:outline-none focus:ring-2 focus:ring-navy-600/18 ' +
  'disabled:cursor-not-allowed disabled:bg-sunken disabled:text-ink-3 ' +
  'aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger/15'

/** Label + optional hint + error, wired by id so the control is announced. */
export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
  id: idProp,
}: {
  label: string
  hint?: React.ReactNode
  error?: string | null
  required?: boolean
  children: (props: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }) => React.ReactNode
  className?: string
  id?: string
}) {
  const auto = useId()
  const id = idProp ?? auto
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="flex items-center gap-1 text-xs font-medium text-ink">
        {label}
        {required && (
          <span aria-hidden className="text-danger">
            *
          </span>
        )}
      </label>
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': !!error })}
      {error ? (
        <p id={`${id}-error`} className="text-2xs text-danger">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="text-2xs leading-relaxed text-ink-3">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cn(control, 'h-9', className)} {...rest} />
  },
)

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(control, 'resize-y py-2 leading-relaxed', className)} {...rest} />
})

/** Native select, styled. Keyboard and screen-reader behaviour comes free. */
export const Select = forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }
>(function Select({ className, options, ...rest }, ref) {
  return (
    <div className="relative">
      <select
        ref={ref}
        className={cn(control, 'h-9 cursor-pointer appearance-none pr-8', className)}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-3"
      />
    </div>
  )
})

export function Switch({
  label,
  description,
  ...rest
}: React.ComponentProps<typeof SwitchPrimitive.Root> & { label: string; description?: string }) {
  const id = useId()
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <label htmlFor={id} className="text-xs font-medium text-ink">
          {label}
        </label>
        {description && <p className="mt-0.5 text-2xs leading-relaxed text-ink-2">{description}</p>}
      </div>
      <SwitchPrimitive.Root
        id={id}
        className={cn(
          'relative h-5 w-9 shrink-0 cursor-pointer rounded-full border border-line-strong',
          'bg-active transition-colors data-[state=checked]:border-navy-600 data-[state=checked]:bg-navy-600',
        )}
        {...rest}
      >
        <SwitchPrimitive.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-raised shadow-sm transition-transform data-[state=checked]:translate-x-4.5" />
      </SwitchPrimitive.Root>
    </div>
  )
}

export function Checkbox({
  className,
  indeterminate,
  ...rest
}: React.ComponentProps<typeof CheckboxPrimitive.Root> & { indeterminate?: boolean }) {
  return (
    <CheckboxPrimitive.Root
      checked={indeterminate ? 'indeterminate' : rest.checked}
      className={cn(
        'grid size-4 shrink-0 place-items-center rounded-[4px] border border-line-strong bg-raised',
        'transition-colors hover:border-ink-3',
        'data-[state=checked]:border-navy-600 data-[state=checked]:bg-navy-600',
        'data-[state=indeterminate]:border-navy-600 data-[state=indeterminate]:bg-navy-600',
        'disabled:cursor-not-allowed disabled:bg-sunken disabled:opacity-60',
        className,
      )}
      {...rest}
    >
      <CheckboxPrimitive.Indicator className="text-ink-inverse">
        {indeterminate ? <Minus className="size-3" /> : <Check className="size-3" strokeWidth={3} />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}
