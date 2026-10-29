import { useEffect } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, MessageSquare } from 'lucide-react'
import { AppShell } from '@/components/shell/AppShell'
import { TopBar } from '@/components/shell/TopBar'
import { Button } from '@/components/ui/Button'
import { EmptyState, Skeleton } from '@/components/ui/Feedback'
import { DocumentViewer } from '@/components/viewer/DocumentViewer'
import { StatusPill } from '@/components/documents/StatusPill'
import { TypeBadge } from '@/components/documents/TypeBadge'
import { useWorkspace } from '@/store/useWorkspace'
import { useComposer } from '@/store/useComposer'
import { useConversations } from '@/store/useConversations'
import { formatBytes, shortDocName } from '@/lib/format'

/**
 * `/doc/:id?page=84&span=s_msa_1223` is a first-class URL — it is how an
 * attorney sends a colleague the exact clause rather than "it's in the MSA
 * somewhere".
 */
export function DocumentPage() {
  const { documentId } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { documents, loading, setType } = useWorkspace()
  const { setScope, setMode } = useComposer()
  const startNew = useConversations((s) => s.startNew)

  const doc = documents.find((d) => d.id === documentId)
  const page = Number(params.get('page') ?? '1') || 1
  const spanId = params.get('span')

  useEffect(() => {
    if (!loading && !doc) navigate('/library', { replace: true })
  }, [loading, doc, navigate])

  return (
    <AppShell showEvidence={false}>
      <TopBar
        title={doc ? shortDocName(doc.name, 60) : 'Document'}
        subtitle={
          doc ? `${doc.pages} pages · ${formatBytes(doc.sizeBytes)}` : undefined
        }
      />

      {loading ? (
        <div className="p-6">
          <Skeleton className="mx-auto h-[36rem] w-full max-w-3xl" />
        </div>
      ) : !doc ? (
        <EmptyState
          title="Document not found"
          body="It may have been deleted."
          action={
            <Button size="sm" variant="secondary" asChild>
              <Link to="/library">Back to the library</Link>
            </Button>
          }
        />
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-canvas px-3 py-2">
            <Button size="xs" variant="ghost" asChild>
              <Link to="/library">
                <ArrowLeft aria-hidden className="size-3.5" />
                Library
              </Link>
            </Button>
            <StatusPill status={doc.status} progress={doc.progress} />
            <TypeBadge
              type={doc.type}
              source={doc.typeSource}
              confidence={doc.classifierConfidence}
              onChange={(t) => void setType(doc.id, t)}
            />
            <Button
              size="xs"
              variant="secondary"
              className="ml-auto"
              disabled={doc.status !== 'ready'}
              onClick={() => {
                startNew()
                setMode('ask')
                setScope({ kind: 'document', documentIds: [doc.id] })
                navigate('/')
              }}
            >
              <MessageSquare aria-hidden className="size-3.5" />
              Ask about this document
            </Button>
          </div>

          <DocumentViewer document={doc} initialPage={page} spanId={spanId} />
        </>
      )}
    </AppShell>
  )
}
