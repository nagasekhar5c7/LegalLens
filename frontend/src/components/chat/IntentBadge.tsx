import { ArrowRight, RefreshCw, Sparkles } from 'lucide-react'
import type { Intent, Mode } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from '@/components/ui/Menu'
import { Tooltip } from '@/components/ui/Floating'

const INTENT_LABEL: Record<Intent, string> = {
  ask: 'Ask',
  summary: 'Summary',
  draft: 'Draft',
  out_of_scope: 'Out of scope',
}

const MODE_LABEL: Record<Mode, string> = {
  auto: 'Auto',
  ask: 'Ask',
  summary: 'Summary',
  draft: 'Draft',
}

/**
 * Nuance #4: auto-routing is never silent.
 *
 * The router is an LLM call. An invisible decision the user cannot see or
 * override is the fastest way to lose trust in a tool whose pitch is
 * transparency — so the decision is shown, and it is one click to overrule.
 */
export function IntentBadge({
  requestedMode,
  intent,
  onRerun,
}: {
  requestedMode: Mode
  intent?: Intent
  onRerun?: (mode: Mode) => void
}) {
  const routed = requestedMode === 'auto' && intent

  if (!routed) {
    return (
      <Badge tone="neutral">
        {MODE_LABEL[requestedMode]}
      </Badge>
    )
  }

  const badge = (
    <Badge tone="navy" className="cursor-pointer gap-1 hover:brightness-[0.97]">
      <Sparkles aria-hidden className="size-2.5" />
      Auto
      <ArrowRight aria-hidden className="size-2.5 opacity-60" />
      <span className="font-semibold">{INTENT_LABEL[intent]}</span>
    </Badge>
  )

  if (!onRerun) return badge

  return (
    <Menu>
      <Tooltip content="Legal Lens chose this mode. Click to run it as something else.">
        <MenuTrigger asChild>{badge}</MenuTrigger>
      </Tooltip>
      <MenuContent align="start">
        <MenuLabel>Run again as</MenuLabel>
        {(['ask', 'summary', 'draft'] as Mode[])
          .filter((m) => m !== intent)
          .map((m) => (
            <MenuItem key={m} onSelect={() => onRerun(m)}>
              <RefreshCw aria-hidden className="size-3.5" />
              {MODE_LABEL[m]}
            </MenuItem>
          ))}
      </MenuContent>
    </Menu>
  )
}
