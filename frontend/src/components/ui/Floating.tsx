import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import * as HoverCardPrimitive from '@radix-ui/react-hover-card'
import { cn } from '@/lib/cn'

export const TooltipProvider = TooltipPrimitive.Provider

const panel =
  'z-50 rounded-card border border-line bg-raised shadow-panel ' +
  'data-[state=open]:animate-[ll-rise_140ms_var(--ease-out-quint)]'

/** Short label on hover/focus. Never the only place information lives. */
export function Tooltip({
  children,
  content,
  side = 'top',
  shortcut,
  align = 'center',
}: {
  children: React.ReactNode
  content: React.ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
  shortcut?: string
}) {
  if (!content) return <>{children}</>
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          align={align}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            'z-50 max-w-64 rounded-control bg-ink px-2 py-1.5 text-2xs leading-snug text-ink-inverse shadow-md',
            'data-[state=delayed-open]:animate-[ll-rise_120ms_var(--ease-out-quint)]',
          )}
        >
          <span className="flex items-center gap-2">
            <span>{content}</span>
            {shortcut && (
              <span className="shrink-0 font-mono text-[10px] opacity-60">{shortcut}</span>
            )}
          </span>
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}

export const Popover = PopoverPrimitive.Root
export const PopoverTrigger = PopoverPrimitive.Trigger
export const PopoverAnchor = PopoverPrimitive.Anchor
export const PopoverClose = PopoverPrimitive.Close

export function PopoverContent({
  className,
  align = 'start',
  side = 'bottom',
  sideOffset = 6,
  ...rest
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        side={side}
        sideOffset={sideOffset}
        collisionPadding={10}
        className={cn(panel, 'max-h-[min(70vh,34rem)] overflow-y-auto', className)}
        {...rest}
      />
    </PopoverPrimitive.Portal>
  )
}

export const HoverCard = HoverCardPrimitive.Root
export const HoverCardTrigger = HoverCardPrimitive.Trigger

/** Used by citation chips: a peek at the excerpt without leaving the answer. */
export function HoverCardContent({
  className,
  side = 'top',
  align = 'center',
  ...rest
}: React.ComponentProps<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal>
      <HoverCardPrimitive.Content
        side={side}
        align={align}
        sideOffset={8}
        collisionPadding={12}
        className={cn(panel, 'w-80 p-0', className)}
        {...rest}
      />
    </HoverCardPrimitive.Portal>
  )
}
