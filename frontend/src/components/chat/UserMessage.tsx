import { useState } from 'react'
import { Check, Pencil, X } from 'lucide-react'
import type { UserTurn } from '@/api/types'
import { Badge } from '@/components/ui/Badge'
import { Button, IconButton } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Field'
import { Tooltip } from '@/components/ui/Floating'
import { DRAFT_TYPE_LABEL } from '@/lib/templates'

const MODE_LABEL = { auto: 'Auto', ask: 'Ask', summary: 'Summary', draft: 'Draft' } as const

export function UserMessage({
  turn,
  onResend,
}: {
  turn: UserTurn
  onResend?: (text: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(turn.text)

  return (
    <div className="group/user px-4 pt-6 sm:px-6">
      <div className="mx-auto max-w-3xl">
        {editing ? (
          <div className="rounded-card border border-navy-200 bg-raised p-2.5 shadow-sm">
            <Textarea
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              className="border-0 px-1 focus:ring-0"
            />
            <div className="mt-1.5 flex justify-end gap-2">
              <Button
                size="xs"
                variant="ghost"
                onClick={() => {
                  setText(turn.text)
                  setEditing(false)
                }}
              >
                <X aria-hidden className="size-3.5" />
                Cancel
              </Button>
              <Button
                size="xs"
                variant="primary"
                disabled={!text.trim() || text === turn.text}
                onClick={() => {
                  setEditing(false)
                  onResend?.(text)
                }}
              >
                <Check aria-hidden className="size-3.5" />
                Ask again
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-end gap-2">
            {onResend && (
              <Tooltip content="Edit and ask again">
                <IconButton
                  label="Edit this question and ask again"
                  size="xs"
                  variant="quiet"
                  className="mt-1 opacity-0 transition-opacity group-hover/user:opacity-100 focus-visible:opacity-100"
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="size-3.5" />
                </IconButton>
              </Tooltip>
            )}
            <div className="max-w-[85%] rounded-card rounded-br-[3px] border border-line bg-raised px-3.5 py-2.5 shadow-sm">
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{turn.text}</p>
              <div className="mt-2 flex flex-wrap items-center justify-end gap-1.5">
                <Badge tone="neutral">{MODE_LABEL[turn.mode]}</Badge>
                {turn.draftParams && (
                  <Badge tone="draft">{DRAFT_TYPE_LABEL[turn.draftParams.draftType]}</Badge>
                )}
                <Badge tone="neutral">
                  {turn.scope.kind === 'workspace'
                    ? 'Whole workspace'
                    : turn.scope.kind === 'document'
                      ? '1 document'
                      : `${turn.scope.documentIds.length} documents`}
                </Badge>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
