import { useState } from 'react'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import type { Feedback, FeedbackReason } from '@/api/types'
import { cn } from '@/lib/cn'
import { Button, IconButton } from '@/components/ui/Button'
import { Chip } from '@/components/ui/Badge'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Floating'
import { Tooltip } from '@/components/ui/Floating'
import { Textarea } from '@/components/ui/Field'

/**
 * FR-25. The reasons are legal-specific on purpose: "wrong citation" and
 * "refused but the answer is there" map directly onto citation precision and
 * false-refusal rate, which are the two metrics the BRD treats as gates.
 */
const REASONS: { id: FeedbackReason; label: string }[] = [
  { id: 'wrong_citation', label: 'Citation does not support the claim' },
  { id: 'missed_content', label: 'Missed content in my documents' },
  { id: 'fabricated', label: 'Stated something not in the sources' },
  { id: 'wrong_refusal', label: 'Refused, but the answer is there' },
  { id: 'wrong_structure', label: 'Wrong structure or tone' },
  { id: 'other', label: 'Something else' },
]

export function FeedbackBar({
  feedback,
  onSubmit,
  className,
}: {
  feedback?: Feedback
  onSubmit: (f: Feedback) => void
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [reasons, setReasons] = useState<FeedbackReason[]>([])
  const [note, setNote] = useState('')

  const verdict = feedback?.verdict

  function toggle(r: FeedbackReason) {
    setReasons((rs) => (rs.includes(r) ? rs.filter((x) => x !== r) : [...rs, r]))
  }

  return (
    <div className={cn('flex items-center gap-0.5', className)}>
      <Tooltip content="Useful">
        <IconButton
          label="Mark this answer useful"
          size="xs"
          variant="quiet"
          aria-pressed={verdict === 'up'}
          onClick={() => onSubmit({ verdict: 'up', reasons: [] })}
          className={cn(verdict === 'up' && 'bg-grounded-bg text-grounded')}
        >
          <ThumbsUp className="size-3.5" />
        </IconButton>
      </Tooltip>

      <Popover open={open} onOpenChange={setOpen}>
        <Tooltip content="Not useful">
          <PopoverTrigger asChild>
            <IconButton
              label="Report a problem with this answer"
              size="xs"
              variant="quiet"
              aria-pressed={verdict === 'down'}
              className={cn(verdict === 'down' && 'bg-danger-bg text-danger')}
            >
              <ThumbsDown className="size-3.5" />
            </IconButton>
          </PopoverTrigger>
        </Tooltip>

        <PopoverContent className="w-80 p-3">
          <p className="text-xs font-semibold text-ink">What went wrong?</p>
          <p className="mt-1 text-2xs leading-relaxed text-ink-3">
            Linked to this turn&rsquo;s retrieval trace, so the exact retrieval can be replayed.
          </p>

          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {REASONS.map((r) => (
              <Chip
                key={r.id}
                tone="danger"
                selected={reasons.includes(r.id)}
                onClick={() => toggle(r.id)}
                className="h-6 text-2xs"
              >
                {r.label}
              </Chip>
            ))}
          </div>

          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="Anything else worth recording (optional)"
            className="mt-2.5 text-xs"
          />

          <div className="mt-2.5 flex justify-end gap-2">
            <Button size="xs" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="xs"
              variant="primary"
              onClick={() => {
                onSubmit({ verdict: 'down', reasons, note: note.trim() || undefined })
                setOpen(false)
                setReasons([])
                setNote('')
              }}
            >
              Send feedback
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
