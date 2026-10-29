import { FileText, PenLine, Search, Sparkles } from 'lucide-react'
import type { Mode } from '@/api/types'
import { Chip } from '@/components/ui/Badge'
import { Tooltip } from '@/components/ui/Floating'
import { MOD } from '@/lib/hotkeys'

const MODES: {
  id: Mode
  label: string
  icon: React.ReactNode
  help: string
  key: string
}[] = [
  {
    id: 'auto',
    label: 'Auto',
    icon: <Sparkles aria-hidden className="size-3.5" />,
    help: 'Legal Lens picks Ask, Summary or Draft from your question — and tells you which it chose.',
    key: '1',
  },
  {
    id: 'ask',
    label: 'Ask',
    icon: <Search aria-hidden className="size-3.5" />,
    help: 'Answer a question from your documents, with a citation behind every claim.',
    key: '2',
  },
  {
    id: 'summary',
    label: 'Summary',
    icon: <FileText aria-hidden className="size-3.5" />,
    help: 'Structured summary of one document, against the template for its type.',
    key: '3',
  },
  {
    id: 'draft',
    label: 'Draft',
    icon: <PenLine aria-hidden className="size-3.5" />,
    help: 'First draft of a memo or case brief — marked as an unreviewed draft throughout.',
    key: '4',
  },
]

export function ModeChips({
  mode,
  onChange,
  disabled,
}: {
  mode: Mode
  onChange: (m: Mode) => void
  disabled?: boolean
}) {
  return (
    <div role="radiogroup" aria-label="Response mode" className="flex items-center gap-1">
      {MODES.map((m) => (
        <Tooltip key={m.id} content={m.help} shortcut={`${MOD} ${m.key}`} side="top">
          <Chip
            role="radio"
            aria-checked={mode === m.id}
            selected={mode === m.id}
            disabled={disabled}
            onClick={() => onChange(m.id)}
            className="h-6.5"
          >
            {m.icon}
            {m.label}
          </Chip>
        </Tooltip>
      ))}
    </div>
  )
}
