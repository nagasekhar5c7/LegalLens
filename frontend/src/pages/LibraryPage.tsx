import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileUp, Trash2 } from 'lucide-react'
import type { LegalDocument } from '@/api/types'
import { pluralise } from '@/lib/format'
import { AppShell } from '@/components/shell/AppShell'
import { TopBar } from '@/components/shell/TopBar'
import { Button } from '@/components/ui/Button'
import { EmptyState, Skeleton } from '@/components/ui/Feedback'
import { UploadDropzone } from '@/components/documents/UploadDropzone'
import { UploadQueue } from '@/components/documents/UploadQueue'
import { DocumentTable } from '@/components/documents/DocumentTable'
import { DocumentDetailDrawer } from '@/components/documents/DocumentDetailDrawer'
import { DeleteDocDialog, DeleteWorkspaceDialog } from '@/components/documents/DeleteDocDialog'
import { useConfig } from '@/store/useConfig'
import { useWorkspace, workspaceHealth } from '@/store/useWorkspace'
import { useComposer } from '@/store/useComposer'
import { useConversations } from '@/store/useConversations'

export function LibraryPage() {
  const navigate = useNavigate()
  const { documents, loading, uploads, upload, clearUploads, setType, remove, removeAll, retry } =
    useWorkspace()
  const { setScope, setMode } = useComposer()
  const startNew = useConversations((s) => s.startNew)

  const [inspecting, setInspecting] = useState<LegalDocument | null>(null)
  const [deleting, setDeleting] = useState<LegalDocument | null>(null)
  const [wipeOpen, setWipeOpen] = useState(false)

  const limits = useConfig((s) => s.config)
  const h = useMemo(() => workspaceHealth(documents), [documents])

  return (
    <AppShell showEvidence={false}>
      <TopBar
        title="Document library"
        subtitle={
          h.documents > 0
            ? `${h.ready} of ${h.documents} searchable · ${h.pages.toLocaleString()} pages indexed · limit ${limits.maxDocuments} documents`
            : 'No documents yet'
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6">
          {loading ? (
            <div className="space-y-2">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : documents.length === 0 ? (
            <>
              <EmptyState
                icon={<FileUp className="size-4" />}
                title="Your workspace is empty"
                body="Legal Lens answers only from documents you add — it has no general knowledge of statutes or case law, by design. Add a contract, opinion or pleading to begin."
              />
              <UploadDropzone onFiles={(f) => void upload(f)} />
            </>
          ) : (
            <>
              <UploadDropzone compact onFiles={(f) => void upload(f)} />

              {uploads.length > 0 && <UploadQueue rows={uploads} onClear={clearUploads} />}

              <section>
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h2 className="text-xs font-semibold text-ink">
                    {pluralise(documents.length, 'document')}
                  </h2>
                  <Button size="xs" variant="ghost" onClick={() => setWipeOpen(true)}>
                    <Trash2 aria-hidden className="size-3.5" />
                    Delete workspace
                  </Button>
                </div>

                <DocumentTable
                  documents={documents}
                  onSetType={(id, t) => void setType(id, t)}
                  onDelete={setDeleting}
                  onRetry={(id) => void retry(id)}
                  onInspect={setInspecting}
                  onAsk={(d) => {
                    startNew()
                    setMode('ask')
                    setScope({ kind: 'document', documentIds: [d.id] })
                    navigate('/')
                  }}
                />
              </section>

              {h.failed > 0 && (
                <p className="rounded-card border border-caution-line bg-caution-bg px-3 py-2 text-2xs leading-relaxed text-caution">
                  <span className="font-semibold">
                    {pluralise(h.failed, 'document')} could not be read
                  </span>{' '}
                  and {h.failed === 1 ? 'is' : 'are'} excluded from every search. Open the ingestion
                  detail for the reason — a 300 dpi rescan of the original usually clears the OCR
                  confidence floor.
                </p>
              )}
            </>
          )}
        </div>
      </div>

      <DocumentDetailDrawer
        document={inspecting}
        open={!!inspecting}
        onOpenChange={(o) => !o && setInspecting(null)}
      />
      <DeleteDocDialog
        document={deleting}
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        onConfirm={() => deleting && void remove(deleting.id)}
      />
      <DeleteWorkspaceDialog
        open={wipeOpen}
        onOpenChange={setWipeOpen}
        documentCount={documents.length}
        onConfirm={() => void removeAll()}
      />
    </AppShell>
  )
}
