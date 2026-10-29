import { Check, Clipboard, Download, FileCode } from 'lucide-react'
import type { AssistantTurn } from '@/api/types'
import {
  DRAFT_MARKING,
  copyText,
  downloadMarkdown,
  slugify,
  turnToMarkdown,
  turnToPlainText,
} from '@/lib/exporting'
import { toast } from '@/store/useToasts'
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from '@/components/ui/Menu'
import { IconButton } from '@/components/ui/Button'
import { Tooltip } from '@/components/ui/Floating'

/**
 * FR-24, and nuance #3: the marking is part of the payload.
 *
 * Every path out of this menu carries the disclaimer and the citation
 * appendix. A draft pasted into Word still announces itself as an unreviewed
 * draft — a banner the user can copy out of is theatre.
 */
export function ExportMenu({ turn }: { turn: AssistantTurn }) {
  const isDraft = turn.kind === 'draft'

  async function copy(kind: 'markdown' | 'plain') {
    const body = kind === 'markdown' ? turnToMarkdown(turn) : turnToPlainText(turn)
    const ok = await copyText(body)
    if (!ok) {
      toast.error('Could not reach the clipboard', 'Your browser blocked clipboard access.')
      return
    }
    toast.done(
      kind === 'markdown' ? 'Copied as Markdown' : 'Copied as plain text',
      isDraft ? 'The unreviewed-draft notice and citation list are included.' : 'Citation list included.',
    )
  }

  const filename = slugify(
    turn.kind === 'draft' ? (turn.draft?.title ?? 'draft') : turn.kind === 'summary' ? `summary-${turn.summary?.documentName ?? ''}` : 'legal-lens-answer',
  )

  return (
    <Menu>
      <Tooltip content="Copy or export">
        <MenuTrigger asChild>
          <IconButton label="Copy or export this output" size="xs" variant="quiet">
            <Clipboard className="size-3.5" />
          </IconButton>
        </MenuTrigger>
      </Tooltip>

      <MenuContent className="min-w-60">
        <MenuLabel>Copy</MenuLabel>
        <MenuItem onSelect={() => void copy('markdown')}>
          <FileCode aria-hidden className="size-3.5" />
          Markdown, with citations
        </MenuItem>
        <MenuItem onSelect={() => void copy('plain')}>
          <Clipboard aria-hidden className="size-3.5" />
          Plain text, with citations
        </MenuItem>

        <MenuSeparator />
        <MenuLabel>Download</MenuLabel>
        <MenuItem onSelect={() => downloadMarkdown(filename, turnToMarkdown(turn))}>
          <Download aria-hidden className="size-3.5" />
          {filename}.md
        </MenuItem>

        <MenuSeparator />
        <div className="flex items-start gap-1.5 px-2 pb-1 pt-1.5">
          <Check aria-hidden className="mt-px size-3 shrink-0 text-grounded" />
          <p className="text-[10px] leading-snug text-ink-3">
            {isDraft
              ? `Every export begins: “${DRAFT_MARKING.slice(0, 28)}…”`
              : 'Exports carry the work-product notice and the citation list.'}
          </p>
        </div>
      </MenuContent>
    </Menu>
  )
}
