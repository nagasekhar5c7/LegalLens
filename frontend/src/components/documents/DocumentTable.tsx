import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowDown,
  ArrowUp,
  Eye,
  MessageSquare,
  MoreHorizontal,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import type { DocStatus, DocType, LegalDocument } from '@/api/types'
import { cn } from '@/lib/cn'
import { DOC_TYPE_LABEL, formatBytes, relativeTime, shortDocName } from '@/lib/format'
import { Chip } from '@/components/ui/Badge'
import { IconButton } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/Feedback'
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from '@/components/ui/Menu'
import { Tooltip } from '@/components/ui/Floating'
import { StatusPill } from './StatusPill'
import { TypeBadge } from './TypeBadge'
import { OcrBadge } from './OcrWarning'

type SortKey = 'name' | 'type' | 'pages' | 'status' | 'uploadedAt'

/** FR-6: name, type, pages, status, upload date — sortable and filterable. */
export function DocumentTable({
  documents,
  onSetType,
  onDelete,
  onRetry,
  onInspect,
  onAsk,
}: {
  documents: LegalDocument[]
  onSetType: (id: string, type: DocType) => void
  onDelete: (doc: LegalDocument) => void
  onRetry: (id: string) => void
  onInspect: (doc: LegalDocument) => void
  onAsk: (doc: LegalDocument) => void
}) {
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({
    key: 'uploadedAt',
    dir: -1,
  })
  const [typeFilter, setTypeFilter] = useState<DocType | 'all'>('all')
  const [statusFilter, setStatusFilter] = useState<DocStatus | 'all'>('all')

  const rows = useMemo(() => {
    const filtered = documents.filter(
      (d) =>
        (typeFilter === 'all' || d.type === typeFilter) &&
        (statusFilter === 'all' || d.status === statusFilter),
    )
    return [...filtered].sort((a, b) => {
      const k = sort.key
      const av = k === 'uploadedAt' ? new Date(a[k]).getTime() : a[k]
      const bv = k === 'uploadedAt' ? new Date(b[k]).getTime() : b[k]
      if (typeof av === 'string' && typeof bv === 'string') return av.localeCompare(bv) * sort.dir
      return ((av as number) - (bv as number)) * sort.dir
    })
  }, [documents, sort, typeFilter, statusFilter])

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: 1 }))
  }

  const Th = ({ k, children, className }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <th scope="col" className={cn('px-3 py-2 text-left font-medium', className)}>
      <button
        type="button"
        onClick={() => toggleSort(k)}
        className="inline-flex items-center gap-1 text-2xs uppercase tracking-wider text-ink-3 hover:text-ink"
      >
        {children}
        {sort.key === k &&
          (sort.dir === 1 ? (
            <ArrowUp aria-hidden className="size-3" />
          ) : (
            <ArrowDown aria-hidden className="size-3" />
          ))}
      </button>
    </th>
  )

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-2xs font-medium text-ink-3">Type</span>
        {(['all', 'contract', 'opinion', 'pleading', 'other'] as const).map((t) => (
          <Chip
            key={t}
            selected={typeFilter === t}
            onClick={() => setTypeFilter(t)}
            className="h-6 text-2xs"
          >
            {t === 'all' ? 'All' : DOC_TYPE_LABEL[t]}
          </Chip>
        ))}
        <span className="mx-1 h-4 w-px bg-line" aria-hidden />
        <span className="mr-1 text-2xs font-medium text-ink-3">Status</span>
        {(['all', 'ready', 'indexing', 'failed'] as const).map((s) => (
          <Chip
            key={s}
            tone={s === 'failed' ? 'danger' : s === 'ready' ? 'grounded' : 'navy'}
            selected={statusFilter === s}
            onClick={() => setStatusFilter(s)}
            className="h-6 text-2xs"
          >
            {s === 'all' ? 'All' : s === 'ready' ? 'Searchable' : s === 'indexing' ? 'Processing' : 'Failed'}
          </Chip>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No documents match these filters"
          body="Clear the filters to see the whole library."
        />
      ) : (
        <div className="overflow-x-auto rounded-card border border-line bg-raised">
          <table className="w-full border-collapse text-xs">
            <thead className="border-b border-line bg-canvas">
              <tr>
                <Th k="name">Document</Th>
                <Th k="type">Type</Th>
                <Th k="pages" className="hidden sm:table-cell">
                  Pages
                </Th>
                <Th k="status">Status</Th>
                <Th k="uploadedAt" className="hidden md:table-cell">
                  Added
                </Th>
                <th scope="col" className="w-10 px-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0 hover:bg-hover">
                  <td className="max-w-0 px-3 py-2.5">
                    <Link
                      to={`/doc/${d.id}`}
                      className="block truncate font-medium text-ink hover:text-navy-600"
                      title={d.name}
                    >
                      {shortDocName(d.name, 52)}
                    </Link>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] text-ink-3">{formatBytes(d.sizeBytes)}</span>
                      {d.ocr && <OcrBadge ocr={d.ocr} />}
                      {d.failure && (
                        <span className="text-[10px] text-danger">{d.failure.reason}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <TypeBadge
                      type={d.type}
                      source={d.typeSource}
                      confidence={d.classifierConfidence}
                      onChange={(t) => onSetType(d.id, t)}
                    />
                  </td>
                  <td className="hidden px-3 py-2.5 font-mono tabular-nums text-ink-2 sm:table-cell">
                    {d.pages}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusPill status={d.status} progress={d.progress} />
                  </td>
                  <td className="hidden whitespace-nowrap px-3 py-2.5 text-ink-3 md:table-cell">
                    {relativeTime(d.uploadedAt)}
                  </td>
                  <td className="px-2 py-2.5">
                    <Menu>
                      <Tooltip content="Document actions">
                        <MenuTrigger asChild>
                          <IconButton label={`Actions for ${d.name}`} size="xs" variant="quiet">
                            <MoreHorizontal className="size-3.5" />
                          </IconButton>
                        </MenuTrigger>
                      </Tooltip>
                      <MenuContent>
                        <MenuItem onSelect={() => onInspect(d)}>
                          <Eye aria-hidden className="size-3.5" />
                          Ingestion detail
                        </MenuItem>
                        <MenuItem disabled={d.status !== 'ready'} onSelect={() => onAsk(d)}>
                          <MessageSquare aria-hidden className="size-3.5" />
                          Ask about this document
                        </MenuItem>
                        {d.status === 'failed' && (
                          <MenuItem onSelect={() => onRetry(d.id)}>
                            <RefreshCw aria-hidden className="size-3.5" />
                            Retry ingestion
                          </MenuItem>
                        )}
                        <MenuSeparator />
                        <MenuItem tone="danger" onSelect={() => onDelete(d)}>
                          <Trash2 aria-hidden className="size-3.5" />
                          Delete
                        </MenuItem>
                      </MenuContent>
                    </Menu>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
