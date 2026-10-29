import { FlaskConical } from 'lucide-react'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import {
  Menu,
  MenuContent,
  MenuLabel,
  MenuRadioGroup,
  MenuRadioItem,
  MenuSeparator,
  MenuTrigger,
} from '@/components/ui/Menu'
import { useUi } from '@/store/useUi'
import { useConfig } from '@/store/useConfig'
import { SCENARIOS } from '@/lib/scenarios'

/**
 * Every failure path, reachable on purpose.
 *
 * This is how a reviewer sees the refusal card, the outage path, the rate limit
 * and the interrupted stream without waiting for one to occur. It is no longer
 * a simulation: the chosen id travels as `X-LegalLens-Scenario` and the
 * backend's fixture engine takes that path for real, so what appears on screen
 * is a genuine response — the same `ApiError`, down the same wire, through the
 * same parser.
 *
 * It is a development affordance, and the server decides whether it exists:
 * `/api/config` reports `scenariosEnabled: false` when `LL_ENV=production`, and
 * the header is ignored there regardless. Rendering nothing in that case beats
 * offering a control the server will not honour.
 */
export function DemoMenu() {
  const { scenario, setScenario } = useUi()
  const enabled = useConfig((s) => s.config.scenariosEnabled)
  const active = SCENARIOS.find((s) => s.id === scenario)!
  const forced = scenario !== 'auto'

  if (!enabled) return null

  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size="xs" variant={forced ? 'secondary' : 'quiet'} className="gap-1.5">
          <FlaskConical aria-hidden className={cn('size-3.5', forced && 'text-caution')} />
          <span className="hidden sm:inline">{forced ? active.label : 'Demo'}</span>
          {forced && <Badge tone="caution" className="h-4 px-1.5">forced</Badge>}
        </Button>
      </MenuTrigger>

      <MenuContent className="w-80">
        <MenuLabel>Response scenario</MenuLabel>
        <MenuRadioGroup value={scenario} onValueChange={(v) => setScenario(v as typeof scenario)}>
          {SCENARIOS.map((s) => (
            <MenuRadioItem key={s.id} value={s.id} className="items-start py-2">
              <span className="min-w-0">
                <span className="block text-xs font-medium text-ink">{s.label}</span>
                <span className="mt-0.5 block text-[10px] leading-snug text-ink-3">
                  {s.description}
                </span>
              </span>
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <p className="px-2 pb-1.5 pt-1 text-[10px] leading-snug text-ink-3">
          Sent to the server as a header, which forces the next response down that path for real.
          Development only — this menu is hidden when the server reports scenarios disabled.
        </p>
      </MenuContent>
    </Menu>
  )
}
