import * as M from '@radix-ui/react-dropdown-menu'
import { Check } from 'lucide-react'
import { cn } from '@/lib/cn'

export const Menu = M.Root
export const MenuTrigger = M.Trigger
export const MenuGroup = M.Group
export const MenuSub = M.Sub
export const MenuSubTrigger = M.SubTrigger

const surface =
  'z-50 min-w-48 overflow-hidden rounded-card border border-line bg-raised p-1 shadow-panel ' +
  'data-[state=open]:animate-[ll-rise_130ms_var(--ease-out-quint)]'

export function MenuContent({
  className,
  align = 'end',
  sideOffset = 6,
  ...rest
}: React.ComponentProps<typeof M.Content>) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={sideOffset}
        collisionPadding={10}
        className={cn(surface, className)}
        {...rest}
      />
    </M.Portal>
  )
}

const itemBase =
  'relative flex cursor-default select-none items-center gap-2 rounded-[5px] px-2 py-1.5 text-xs ' +
  'outline-none data-[highlighted]:bg-hover data-[disabled]:pointer-events-none data-[disabled]:opacity-45'

export function MenuItem({
  className,
  tone = 'default',
  shortcut,
  children,
  ...rest
}: React.ComponentProps<typeof M.Item> & { tone?: 'default' | 'danger'; shortcut?: string }) {
  return (
    <M.Item
      className={cn(
        itemBase,
        tone === 'danger'
          ? 'text-danger data-[highlighted]:bg-danger-bg'
          : 'text-ink-2 data-[highlighted]:text-ink',
        className,
      )}
      {...rest}
    >
      {children}
      {shortcut && (
        <span className="ml-auto pl-3 font-mono text-[10px] text-ink-3">{shortcut}</span>
      )}
    </M.Item>
  )
}

export function MenuCheckItem({
  className,
  children,
  ...rest
}: React.ComponentProps<typeof M.CheckboxItem>) {
  return (
    <M.CheckboxItem className={cn(itemBase, 'pl-7 text-ink-2', className)} {...rest}>
      <M.ItemIndicator className="absolute left-2">
        <Check className="size-3.5 text-navy-600" />
      </M.ItemIndicator>
      {children}
    </M.CheckboxItem>
  )
}

export function MenuRadioGroup({ children, ...rest }: React.ComponentProps<typeof M.RadioGroup>) {
  return <M.RadioGroup {...rest}>{children}</M.RadioGroup>
}

export function MenuRadioItem({
  className,
  children,
  ...rest
}: React.ComponentProps<typeof M.RadioItem>) {
  return (
    <M.RadioItem className={cn(itemBase, 'pl-7 text-ink-2', className)} {...rest}>
      <M.ItemIndicator className="absolute left-2">
        <Check className="size-3.5 text-navy-600" />
      </M.ItemIndicator>
      {children}
    </M.RadioItem>
  )
}

export function MenuLabel({ className, ...rest }: React.ComponentProps<typeof M.Label>) {
  return (
    <M.Label
      className={cn(
        'px-2 pb-1 pt-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-3',
        className,
      )}
      {...rest}
    />
  )
}

export function MenuSeparator({ className, ...rest }: React.ComponentProps<typeof M.Separator>) {
  return <M.Separator className={cn('-mx-1 my-1 h-px bg-line', className)} {...rest} />
}

export function MenuSubContent({ className, ...rest }: React.ComponentProps<typeof M.SubContent>) {
  return (
    <M.Portal>
      <M.SubContent className={cn(surface, className)} sideOffset={4} {...rest} />
    </M.Portal>
  )
}
