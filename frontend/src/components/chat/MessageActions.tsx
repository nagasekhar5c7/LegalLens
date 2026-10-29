import { Activity, RefreshCw } from 'lucide-react'
import type { AssistantTurn, Mode } from '@/api/types'
import { formatMs, formatUsd } from '@/lib/format'
import { IconButton } from '@/components/ui/Button'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from '@/components/ui/Menu'
import { Tooltip } from '@/components/ui/Floating'
import { ExportMenu } from '@/components/answer/ExportMenu'
import { FeedbackBar } from './FeedbackBar'
import { useUi } from '@/store/useUi'
import { useConversations } from '@/store/useConversations'

const MODE_LABEL: Record<Mode, string> = {
  auto: 'Auto',
  ask: 'Ask',
  summary: 'Summary',
  draft: 'Draft',
}

/** The row under every assistant turn: verify, export, retry, inspect. */
export function MessageActions({ turn }: { turn: AssistantTurn }) {
  const setTraceTurnId = useUi((s) => s.setTraceTurnId)
  const { setFeedback, regenerate, streamingTurnId } = useConversations()
  const busy = streamingTurnId !== null

  return (
    <div className="mt-3 flex flex-wrap items-center gap-1 border-t border-line pt-2 print:hidden">
      <FeedbackBar
        feedback={turn.feedback}
        onSubmit={(f) => void setFeedback(turn.id, f)}
      />

      <span className="mx-1 h-4 w-px bg-line" aria-hidden />

      <ExportMenu turn={turn} />

      <Menu>
        <Tooltip content="Run this again">
          <MenuTrigger asChild>
            <IconButton label="Regenerate this response" size="xs" variant="quiet" disabled={busy}>
              <RefreshCw className="size-3.5" />
            </IconButton>
          </MenuTrigger>
        </Tooltip>
        <MenuContent align="start">
          <MenuLabel>Regenerate</MenuLabel>
          <MenuItem onSelect={() => void regenerate(turn.id)}>
            <RefreshCw aria-hidden className="size-3.5" />
            Same mode ({MODE_LABEL[turn.requestedMode]})
          </MenuItem>
          {(['ask', 'summary', 'draft'] as Mode[])
            .filter((m) => m !== (turn.intent ?? turn.requestedMode))
            .map((m) => (
              <MenuItem key={m} onSelect={() => void regenerate(turn.id, m)}>
                <RefreshCw aria-hidden className="size-3.5" />
                As {MODE_LABEL[m]}
              </MenuItem>
            ))}
        </MenuContent>
      </Menu>

      {turn.usage && (
        <Tooltip
          content={`Open the trace — stage timings, retrieved counts, model and index versions`}
        >
          <button
            type="button"
            onClick={() => setTraceTurnId(turn.id)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-[5px] px-1.5 py-1 font-mono text-[10px] text-ink-3 hover:bg-hover hover:text-ink-2"
          >
            <Activity aria-hidden className="size-3" />
            {formatMs(turn.usage.latencyMs)} · {formatUsd(turn.usage.usd)}
          </button>
        </Tooltip>
      )}
    </div>
  )
}
