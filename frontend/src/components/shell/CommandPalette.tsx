import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  FileText,
  FileUp,
  Library,
  PenLine,
  Plus,
  Search,
  Settings,
  Sparkles,
} from 'lucide-react'
import type { Mode } from '@/api/types'
import { cn } from '@/lib/cn'
import { shortDocName } from '@/lib/format'
import { Dialog, DialogContent } from '@/components/ui/Overlay'
import { Kbd } from '@/components/ui/Feedback'
import { useUi } from '@/store/useUi'
import { useConversations } from '@/store/useConversations'
import { useWorkspace } from '@/store/useWorkspace'
import { useComposer } from '@/store/useComposer'

interface Command {
  id: string
  label: string
  group: string
  icon: React.ReactNode
  hint?: string
  run: () => void
}

export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, setUploadOpen } = useUi()
  const { list, startNew } = useConversations()
  const documents = useWorkspace((s) => s.documents)
  const { setMode } = useComposer()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [index, setIndex] = useState(0)

  useEffect(() => {
    if (paletteOpen) {
      setQuery('')
      setIndex(0)
    }
  }, [paletteOpen])

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => {
      setPaletteOpen(false)
      navigate(to)
    }
    const actions: Command[] = [
      {
        id: 'new',
        label: 'New chat',
        group: 'Actions',
        icon: <Plus aria-hidden className="size-3.5" />,
        run: () => {
          setPaletteOpen(false)
          startNew()
          navigate('/')
        },
      },
      {
        id: 'upload',
        label: 'Upload documents',
        group: 'Actions',
        icon: <FileUp aria-hidden className="size-3.5" />,
        run: () => {
          setPaletteOpen(false)
          setUploadOpen(true)
        },
      },
      {
        id: 'library',
        label: 'Document library',
        group: 'Actions',
        icon: <Library aria-hidden className="size-3.5" />,
        run: go('/library'),
      },
      {
        id: 'settings',
        label: 'Settings, data & privacy',
        group: 'Actions',
        icon: <Settings aria-hidden className="size-3.5" />,
        run: go('/settings'),
      },
      ...(['auto', 'ask', 'summary', 'draft'] as Mode[]).map((m) => ({
        id: `mode-${m}`,
        label: `Switch to ${m === 'auto' ? 'Auto' : m[0].toUpperCase() + m.slice(1)} mode`,
        group: 'Mode',
        icon:
          m === 'draft' ? (
            <PenLine aria-hidden className="size-3.5" />
          ) : m === 'summary' ? (
            <FileText aria-hidden className="size-3.5" />
          ) : m === 'ask' ? (
            <Search aria-hidden className="size-3.5" />
          ) : (
            <Sparkles aria-hidden className="size-3.5" />
          ),
        run: () => {
          setPaletteOpen(false)
          setMode(m)
          document.getElementById('prompt')?.focus()
        },
      })),
    ]

    const convs: Command[] = list.slice(0, 20).map((c) => ({
      id: `c-${c.id}`,
      label: c.title,
      group: 'Conversations',
      icon: <ArrowRight aria-hidden className="size-3.5" />,
      run: go(`/c/${c.id}`),
    }))

    const docs: Command[] = documents.map((d) => ({
      id: `d-${d.id}`,
      label: shortDocName(d.name, 48),
      group: 'Documents',
      icon: <FileText aria-hidden className="size-3.5" />,
      hint: `${d.pages} pages · ${d.status}`,
      run: go(`/doc/${d.id}`),
    }))

    return [...actions, ...convs, ...docs]
  }, [list, documents, navigate, setPaletteOpen, setUploadOpen, startNew, setMode])

  const filtered = useMemo(() => {
    if (!query.trim()) return commands
    const q = query.toLowerCase()
    return commands.filter(
      (c) => c.label.toLowerCase().includes(q) || c.group.toLowerCase().includes(q),
    )
  }, [commands, query])

  const grouped = useMemo(() => {
    const map = new Map<string, Command[]>()
    for (const c of filtered) map.set(c.group, [...(map.get(c.group) ?? []), c])
    return [...map.entries()]
  }, [filtered])

  let flat = 0

  return (
    <Dialog open={paletteOpen} onOpenChange={setPaletteOpen}>
      <DialogContent title="Command palette" size="md" hideClose className="max-h-[32rem]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <Search aria-hidden className="size-4 shrink-0 text-ink-3" />
          <input
            autoFocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setIndex(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setIndex((i) => Math.min(filtered.length - 1, i + 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setIndex((i) => Math.max(0, i - 1))
              } else if (e.key === 'Enter') {
                e.preventDefault()
                filtered[index]?.run()
              }
            }}
            placeholder="Search conversations, documents and actions…"
            className="w-full bg-transparent text-sm text-ink placeholder:text-ink-3 focus:outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>

        <div className="overflow-y-auto p-1.5">
          {grouped.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-ink-3">Nothing matches “{query}”.</p>
          )}
          {grouped.map(([group, items]) => (
            <section key={group} className="mb-1">
              <h3 className="px-2 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-3">
                {group}
              </h3>
              <ul>
                {items.map((c) => {
                  const i = flat++
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        onMouseEnter={() => setIndex(i)}
                        onClick={c.run}
                        className={cn(
                          'flex w-full items-center gap-2.5 rounded-control px-2 py-1.5 text-left',
                          i === index ? 'bg-hover text-ink' : 'text-ink-2',
                        )}
                      >
                        <span className="shrink-0 text-ink-3">{c.icon}</span>
                        <span className="min-w-0 flex-1 truncate text-xs">{c.label}</span>
                        {c.hint && (
                          <span className="shrink-0 text-[10px] text-ink-3">{c.hint}</span>
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}
