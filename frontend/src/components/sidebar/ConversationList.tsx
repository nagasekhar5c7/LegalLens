import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  FileText,
  MoreHorizontal,
  PenLine,
  Pin,
  PinOff,
  Search,
  Trash2,
  Pencil,
} from 'lucide-react'
import type { ConversationSummary } from '@/api/types'
import { cn } from '@/lib/cn'
import { CONVERSATION_BUCKETS, bucketFor, relativeTime } from '@/lib/format'
import { IconButton, Button } from '@/components/ui/Button'
import { Skeleton, EmptyState } from '@/components/ui/Feedback'
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from '@/components/ui/Menu'
import { Dialog, DialogContent } from '@/components/ui/Overlay'
import { useConversations } from '@/store/useConversations'

const KIND_ICON = {
  answer: <Search aria-hidden className="size-3" />,
  summary: <FileText aria-hidden className="size-3" />,
  draft: <PenLine aria-hidden className="size-3" />,
} as const

export function ConversationList({
  conversations,
  loading,
  activeId,
  query,
}: {
  conversations: ConversationSummary[]
  loading: boolean
  activeId?: string
  query: string
}) {
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null)
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null)
  const [draftTitle, setDraftTitle] = useState('')
  const { rename, setPinned, remove } = useConversations()
  const navigate = useNavigate()

  if (loading) {
    return (
      <div className="space-y-1 px-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  const filtered = query
    ? conversations.filter((c) => c.title.toLowerCase().includes(query.toLowerCase()))
    : conversations

  if (filtered.length === 0) {
    return (
      <EmptyState
        tone="quiet"
        title={query ? 'No matching conversations' : 'No conversations yet'}
        body={query ? 'Try a different search.' : 'Your threads will appear here.'}
      />
    )
  }

  const pinned = filtered.filter((c) => c.pinned)
  const rest = filtered.filter((c) => !c.pinned)

  const groups: { label: string; items: ConversationSummary[] }[] = []
  if (pinned.length > 0) groups.push({ label: 'Pinned', items: pinned })
  for (const bucket of CONVERSATION_BUCKETS) {
    const items = rest.filter((c) => bucketFor(c.lastMessageAt) === bucket)
    if (items.length > 0) groups.push({ label: bucket, items })
  }

  return (
    <>
      <nav aria-label="Conversations" className="px-2">
        {groups.map((group) => (
          <section key={group.label} className="mb-2">
            <h3 className="px-1.5 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
              {group.label}
            </h3>
            <ul>
              {group.items.map((c) => {
                const active = c.id === activeId
                return (
                  <li key={c.id} className="group/row relative">
                    <Link
                      to={`/c/${c.id}`}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'flex items-center gap-2 rounded-control py-1.5 pl-2 pr-7 transition-colors',
                        active ? 'bg-active text-ink' : 'text-ink-2 hover:bg-hover hover:text-ink',
                      )}
                    >
                      <span className={cn('shrink-0', active ? 'text-navy-600' : 'text-ink-3')}>
                        {KIND_ICON[c.dominantKind]}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs">{c.title}</span>
                      {c.pinned && <Pin aria-hidden className="size-2.5 shrink-0 text-ink-3" />}
                      <span className="shrink-0 text-[10px] text-ink-3 group-hover/row:opacity-0">
                        {relativeTime(c.lastMessageAt)}
                      </span>
                    </Link>

                    <Menu>
                      <MenuTrigger asChild>
                        <IconButton
                          label={`Options for ${c.title}`}
                          size="xs"
                          variant="quiet"
                          className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 transition-opacity group-hover/row:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
                        >
                          <MoreHorizontal className="size-3.5" />
                        </IconButton>
                      </MenuTrigger>
                      <MenuContent>
                        <MenuItem
                          onSelect={() => {
                            setDraftTitle(c.title)
                            setRenaming(c)
                          }}
                        >
                          <Pencil aria-hidden className="size-3.5" />
                          Rename
                        </MenuItem>
                        <MenuItem onSelect={() => void setPinned(c.id, !c.pinned)}>
                          {c.pinned ? (
                            <>
                              <PinOff aria-hidden className="size-3.5" />
                              Unpin
                            </>
                          ) : (
                            <>
                              <Pin aria-hidden className="size-3.5" />
                              Pin to top
                            </>
                          )}
                        </MenuItem>
                        <MenuSeparator />
                        <MenuItem tone="danger" onSelect={() => setDeleting(c)}>
                          <Trash2 aria-hidden className="size-3.5" />
                          Delete
                        </MenuItem>
                      </MenuContent>
                    </Menu>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </nav>

      <Dialog open={!!renaming} onOpenChange={(o) => !o && setRenaming(null)}>
        {renaming && (
          <DialogContent
            title="Rename conversation"
            size="sm"
            footer={
              <>
                <Button size="sm" variant="ghost" onClick={() => setRenaming(null)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!draftTitle.trim()}
                  onClick={() => {
                    void rename(renaming.id, draftTitle.trim())
                    setRenaming(null)
                  }}
                >
                  Save
                </Button>
              </>
            }
          >
            <div className="p-5">
              <label htmlFor="rename" className="text-xs font-medium text-ink">
                Title
              </label>
              <input
                id="rename"
                autoFocus
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                className="mt-1.5 h-9 w-full rounded-control border border-line-strong bg-raised px-2.5 text-sm focus:border-navy-600 focus:outline-none focus:ring-2 focus:ring-navy-600/18"
              />
            </div>
          </DialogContent>
        )}
      </Dialog>

      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        {deleting && (
          <DialogContent
            title="Delete this conversation?"
            description="The thread and its responses are removed. Your documents are not affected."
            size="sm"
            footer={
              <>
                <Button size="sm" variant="ghost" onClick={() => setDeleting(null)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => {
                    const wasActive = deleting.id === activeId
                    void remove(deleting.id)
                    setDeleting(null)
                    if (wasActive) navigate('/')
                  }}
                >
                  <Trash2 aria-hidden className="size-3.5" />
                  Delete conversation
                </Button>
              </>
            }
          >
            <p className="px-5 py-4 text-xs leading-relaxed text-ink-2">
              <span className="font-medium text-ink">{deleting.title}</span> — {deleting.turnCount}{' '}
              message{deleting.turnCount === 1 ? '' : 's'}. This cannot be undone.
            </p>
          </DialogContent>
        )}
      </Dialog>
    </>
  )
}
