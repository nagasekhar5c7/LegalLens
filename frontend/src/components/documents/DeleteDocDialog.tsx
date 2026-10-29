import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import type { LegalDocument } from '@/api/types'
import { Button } from '@/components/ui/Button'
import { Dialog, DialogContent } from '@/components/ui/Overlay'
import { Input } from '@/components/ui/Field'

/**
 * FR-26 / TS-6, and nuance #10.
 *
 * "Delete" in a product holding client confidences is a trust surface, not a
 * menu item. The dialog enumerates exactly what is removed, because the
 * attorney has to be able to rely on this under Rule 1.1 cmt. 8 — and because
 * a vague promise is indistinguishable from a soft delete.
 */
const REMOVED = [
  ['The original file', 'from encrypted blob storage'],
  ['Extracted text', 'including page and position metadata'],
  ['Chunks', 'every passage derived from the document'],
  ['Embeddings', 'removed from the vector index, effective immediately for retrieval'],
]

export function DeleteDocDialog({
  document: doc,
  open,
  onOpenChange,
  onConfirm,
}: {
  document: LegalDocument | null
  open: boolean
  onOpenChange: (o: boolean) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {doc && (
        <DialogContent
          title="Delete this document?"
          description="This is a hard delete, not an archive. It cannot be undone."
          size="md"
          footer={
            <>
              <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  onConfirm()
                  onOpenChange(false)
                }}
              >
                <Trash2 aria-hidden className="size-3.5" />
                Delete permanently
              </Button>
            </>
          }
        >
          <div className="p-5">
            <p className="text-xs font-medium text-ink">{doc.name}</p>
            <p className="mt-0.5 text-2xs text-ink-3">
              {doc.pages} pages · uploaded {new Date(doc.uploadedAt).toLocaleDateString()}
            </p>

            <h3 className="mt-4 text-2xs font-semibold uppercase tracking-wider text-ink-3">
              What gets removed
            </h3>
            <ul className="mt-2 divide-y divide-line rounded-card border border-line">
              {REMOVED.map(([what, detail]) => (
                <li key={what} className="flex items-baseline gap-2 px-2.5 py-2">
                  <span className="shrink-0 text-xs font-medium text-ink">{what}</span>
                  <span className="min-w-0 text-2xs text-ink-3">{detail}</span>
                </li>
              ))}
            </ul>

            <p className="mt-3 text-2xs leading-relaxed text-ink-2">
              Past conversations that cited this document are kept, but their citations will no
              longer resolve to a source — the answers become unverifiable.
            </p>
          </div>
        </DialogContent>
      )}
    </Dialog>
  )
}

/** Workspace-level deletion gets a typed confirmation. */
export function DeleteWorkspaceDialog({
  open,
  onOpenChange,
  documentCount,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  documentCount: number
  onConfirm: () => void
}) {
  const [typed, setTyped] = useState('')
  const armed = typed.trim().toUpperCase() === 'DELETE'

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setTyped('')
        onOpenChange(o)
      }}
    >
      <DialogContent
        title="Delete the entire workspace?"
        description="Every document, every conversation, every embedding. This cannot be undone."
        size="sm"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="danger"
              disabled={!armed}
              onClick={() => {
                onConfirm()
                onOpenChange(false)
              }}
            >
              <Trash2 aria-hidden className="size-3.5" />
              Delete everything
            </Button>
          </>
        }
      >
        <div className="p-5">
          <p className="text-xs leading-relaxed text-ink-2">
            {documentCount} document{documentCount === 1 ? '' : 's'}, their extracted text, chunks and
            embeddings, and all conversation history will be removed.
          </p>
          <label htmlFor="confirm-delete" className="mt-4 block text-xs font-medium text-ink">
            Type DELETE to confirm
          </label>
          <Input
            id="confirm-delete"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="DELETE"
            className="mt-1.5"
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}
