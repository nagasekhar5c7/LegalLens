import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Library, LogOut, PanelLeftClose, Plus, Scale, Search, Settings, X } from 'lucide-react'
import { MOD, useHotkey } from '@/lib/hotkeys'
import { Button, IconButton } from '@/components/ui/Button'
import { Kbd, Spinner } from '@/components/ui/Feedback'
import { Tooltip } from '@/components/ui/Floating'
import { useConversations } from '@/store/useConversations'
import { useWorkspace, workspaceHealth } from '@/store/useWorkspace'
import { useUi } from '@/store/useUi'
import { useSession } from '@/store/useSession'
import { ConversationList } from './ConversationList'
import { WorkspaceHealthCard } from './WorkspaceHealthCard'

export function Sidebar({ activeId }: { activeId?: string }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const { list, listLoading, startNew, search, loadMore, nextCursor, loadingMore } =
    useConversations()
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const documents = useWorkspace((s) => s.documents)
  const health = useMemo(() => workspaceHealth(documents), [documents])
  const { toggleSidebar } = useUi()
  const signOut = useSession((s) => s.signOut)

  function newChat() {
    startNew()
    navigate('/')
    window.setTimeout(() => document.getElementById('prompt')?.focus(), 40)
  }

  useHotkey('mod+shift+o', (e) => {
    e.preventDefault()
    newChat()
  })

  /* Typing filters what is loaded immediately; the server is asked a beat
     later, which is what finds the thread that was never paged in. */
  useEffect(() => {
    const id = window.setTimeout(() => void search(query.trim()), 250)
    return () => window.clearTimeout(id)
  }, [query, search])

  /* US-10: page the list rather than loading a year of threads at once. */
  useEffect(() => {
    const target = sentinel.current
    if (!target || !nextCursor) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadMore()
      },
      { root: scroller.current, rootMargin: '200px' },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [nextCursor, loadMore])

  return (
    <div className="flex min-h-0 flex-col border-r border-line bg-canvas print:hidden">
      <div className="flex h-12 shrink-0 items-center gap-2 px-3">
        <Link to="/" className="flex min-w-0 items-center gap-2 rounded-control py-1 pr-1">
          <span
            aria-hidden
            className="grid size-5 shrink-0 place-items-center rounded-[5px] bg-navy-600"
          >
            <Scale className="size-3 text-ink-inverse" />
          </span>
          <span className="truncate text-xs font-semibold tracking-[-0.01em] text-ink">
            Legal Lens
          </span>
        </Link>
        <Tooltip content="Hide the sidebar" shortcut="[">
          <IconButton
            label="Hide the sidebar"
            size="xs"
            variant="quiet"
            className="ml-auto"
            onClick={toggleSidebar}
          >
            <PanelLeftClose className="size-4" />
          </IconButton>
        </Tooltip>
      </div>

      <div className="shrink-0 px-2 pb-2">
        <Button variant="primary" size="sm" className="w-full justify-start" onClick={newChat}>
          <Plus aria-hidden className="size-3.5" />
          New chat
          <Kbd className="ml-auto border-navy-500/60 bg-navy-700/40 text-navy-100">
            {MOD}⇧O
          </Kbd>
        </Button>
      </div>

      <div className="relative shrink-0 px-2 pb-1.5">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-4 top-1/2 size-3.5 -translate-y-1/2 text-ink-3"
        />
        <label htmlFor="conv-search" className="sr-only">
          Search conversations
        </label>
        <input
          id="conv-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations"
          className="h-7.5 w-full rounded-control border border-transparent bg-sunken pl-7 pr-7 text-xs text-ink placeholder:text-ink-3 hover:bg-hover focus:border-navy-600 focus:bg-raised focus:outline-none"
        />
        {query && (
          <IconButton
            label="Clear search"
            size="xs"
            variant="quiet"
            className="absolute right-3 top-1/2 -translate-y-1/2"
            onClick={() => setQuery('')}
          >
            <X className="size-3" />
          </IconButton>
        )}
      </div>

      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
        <ConversationList
          conversations={list}
          loading={listLoading}
          activeId={activeId}
          query={query}
        />
        {nextCursor && (
          <div ref={sentinel} className="flex justify-center py-3" aria-hidden={!loadingMore}>
            {loadingMore && <Spinner className="size-3.5 text-ink-3" />}
          </div>
        )}
      </div>

      <div className="shrink-0 space-y-1.5 border-t border-line p-2">
        <WorkspaceHealthCard health={health} />
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" className="flex-1 justify-start" asChild>
            <Link to="/library">
              <Library aria-hidden className="size-3.5" />
              Documents
              <Kbd className="ml-auto">{MOD}L</Kbd>
            </Link>
          </Button>
          <Tooltip content="Settings, data & privacy">
            <IconButton label="Settings" size="sm" variant="ghost" asChild>
              <Link to="/settings">
                <Settings className="size-4" />
              </Link>
            </IconButton>
          </Tooltip>
          <Tooltip content="Sign out">
            <IconButton
              label="Sign out"
              size="sm"
              variant="ghost"
              onClick={() => void signOut()}
            >
              <LogOut className="size-4" />
            </IconButton>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}
