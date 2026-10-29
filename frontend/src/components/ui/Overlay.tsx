import * as DialogPrimitive from '@radix-ui/react-dialog'
import { VisuallyHidden } from '@radix-ui/react-visually-hidden'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { IconButton } from './Button'

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close

function Scrim() {
  return (
    <DialogPrimitive.Overlay
      className="fixed inset-0 z-40 bg-scrim backdrop-blur-[1px] data-[state=open]:animate-[ll-rise_150ms_ease-out]"
    />
  )
}

const WIDTH = {
  sm: 'max-w-md',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
  full: 'max-w-[min(92rem,calc(100vw-4rem))]',
} as const

/**
 * Centred modal. Radix handles focus trap, restore, and Esc — which is the
 * reason we are not hand-rolling this (NFR-13).
 */
export function DialogContent({
  title,
  description,
  size = 'md',
  className,
  children,
  footer,
  hideClose,
}: {
  title: string
  description?: React.ReactNode
  size?: keyof typeof WIDTH
  className?: string
  children: React.ReactNode
  footer?: React.ReactNode
  hideClose?: boolean
}) {
  return (
    <DialogPrimitive.Portal>
      <Scrim />
      <DialogPrimitive.Content
        className={cn(
          'fixed left-1/2 top-1/2 z-50 flex max-h-[min(88vh,52rem)] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col',
          'rounded-panel border border-line bg-raised shadow-panel',
          'data-[state=open]:animate-[ll-rise_170ms_var(--ease-out-quint)]',
          WIDTH[size],
          className,
        )}
      >
        <header className="flex items-start gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <DialogPrimitive.Title className="text-sm font-semibold text-ink">
              {title}
            </DialogPrimitive.Title>
            {description && (
              <DialogPrimitive.Description className="mt-1 text-xs leading-relaxed text-ink-2">
                {description}
              </DialogPrimitive.Description>
            )}
          </div>
          {!hideClose && (
            <DialogPrimitive.Close asChild>
              <IconButton label="Close" size="sm" className="-mr-1 -mt-0.5">
                <X className="size-4" />
              </IconButton>
            </DialogPrimitive.Close>
          )}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-line bg-canvas px-5 py-3">
            {footer}
          </footer>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

/** Edge-docked panel: draft parameters, document detail, trace. */
export function SheetContent({
  title,
  description,
  side = 'right',
  width = 'w-[min(30rem,calc(100vw-2rem))]',
  className,
  children,
  footer,
  bare,
}: {
  title: string
  description?: React.ReactNode
  side?: 'right' | 'left' | 'bottom'
  width?: string
  className?: string
  children: React.ReactNode
  footer?: React.ReactNode
  /** The content supplies its own header; title stays for screen readers. */
  bare?: boolean
}) {
  const pos =
    side === 'right'
      ? cn('inset-y-0 right-0 border-l', width)
      : side === 'left'
        ? cn('inset-y-0 left-0 border-r', width)
        : 'inset-x-0 bottom-0 max-h-[85vh] border-t rounded-t-panel'
  return (
    <DialogPrimitive.Portal>
      <Scrim />
      <DialogPrimitive.Content
        className={cn(
          'fixed z-50 flex flex-col border-line bg-raised shadow-panel',
          'data-[state=open]:animate-[ll-rise_170ms_var(--ease-out-quint)]',
          pos,
          className,
        )}
      >
        {bare ? (
          <VisuallyHidden>
            <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
          </VisuallyHidden>
        ) : (
          <header className="flex items-start gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0 flex-1">
              <DialogPrimitive.Title className="text-sm font-semibold text-ink">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1 text-xs leading-relaxed text-ink-2">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <IconButton label="Close" size="sm" className="-mr-1">
                <X className="size-4" />
              </IconButton>
            </DialogPrimitive.Close>
          </header>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        {footer && (
          <footer className="flex items-center justify-end gap-2 border-t border-line bg-canvas px-4 py-3">
            {footer}
          </footer>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}
