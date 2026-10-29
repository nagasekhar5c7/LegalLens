import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { useHotkey } from '@/lib/hotkeys'
import { BREAKPOINT, useMediaQuery } from '@/lib/useMediaQuery'
import { Dialog, SheetContent } from '@/components/ui/Overlay'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { EvidencePanel } from '@/components/evidence/EvidencePanel'
import { UploadDialog } from '@/components/documents/UploadDialog'
import { TraceDrawer } from '@/components/trace/TraceDrawer'
import { CommandPalette } from './CommandPalette'
import { PanelResizer } from './PanelResizer'
import { useUi } from '@/store/useUi'
import { useEvidence } from '@/store/useEvidence'
import { useWorkspace } from '@/store/useWorkspace'
import { useConversations } from '@/store/useConversations'

/**
 * Three panes: conversations │ work │ evidence.
 *
 * Responsive behaviour is a deliberate compromise. Below 1280px the evidence
 * panel becomes an overlay; below 1024px the sidebar does too. The
 * evidence-beside-answer promise degrades on a phone, and pretending otherwise
 * would be worse than saying so.
 */
export function AppShell({
  children,
  showEvidence = true,
}: {
  children: React.ReactNode
  showEvidence?: boolean
}) {
  const location = useLocation()
  const navigate = useNavigate()
  const {
    sidebarOpen,
    evidenceOpen,
    sidebarWidth,
    evidenceWidth,
    setSidebarWidth,
    setEvidenceWidth,
    toggleSidebar,
    toggleEvidence,
    setPaletteOpen,
    setUploadOpen,
  } = useUi()

  const threePane = useMediaQuery(BREAKPOINT.threePane)
  const twoPane = useMediaQuery(BREAKPOINT.twoPane)

  const loadDocuments = useWorkspace((s) => s.load)
  const loadList = useConversations((s) => s.loadList)
  const cycle = useEvidence((s) => s.cycle)

  useEffect(() => {
    void loadDocuments()
    void loadList()
  }, [loadDocuments, loadList])

  const activeId = location.pathname.startsWith('/c/')
    ? location.pathname.slice(3)
    : undefined

  useHotkey('mod+k', (e) => {
    e.preventDefault()
    setPaletteOpen(true)
  }, { allowInEditable: true })
  useHotkey('mod+u', (e) => {
    e.preventDefault()
    setUploadOpen(true)
  }, { allowInEditable: true })
  useHotkey('mod+l', (e) => {
    e.preventDefault()
    navigate('/library')
  }, { allowInEditable: true })
  useHotkey('[', toggleSidebar)
  useHotkey(']', toggleEvidence)

  /* ⌥↑/↓ cycles citations in the current answer and opens the one it lands on
     in the evidence panel — verification without the mouse (NFR-13). */
  useHotkey(
    'alt+arrowdown',
    (e) => {
      e.preventDefault()
      const c = cycle(1)
      if (c) {
        useUi.getState().setEvidenceOpen(true)
        document
          .querySelector(`[data-excerpt="${c.spanId}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    },
    { allowInEditable: true },
  )
  useHotkey(
    'alt+arrowup',
    (e) => {
      e.preventDefault()
      const c = cycle(-1)
      if (c) {
        useUi.getState().setEvidenceOpen(true)
        document
          .querySelector(`[data-excerpt="${c.spanId}"]`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    },
    { allowInEditable: true },
  )

  const sidebarInGrid = twoPane && sidebarOpen
  const evidenceInGrid = threePane && evidenceOpen && showEvidence
  const evidenceOverlay = !threePane && evidenceOpen && showEvidence

  return (
    <div className="flex h-dvh w-full overflow-hidden bg-canvas">
      {sidebarInGrid && (
        <>
          <div style={{ width: sidebarWidth }} className="shrink-0">
            <Sidebar activeId={activeId} />
          </div>
          <PanelResizer
            side="left"
            width={sidebarWidth}
            onResize={setSidebarWidth}
            label="Resize the sidebar"
          />
        </>
      )}

      {/* Below 1024px the sidebar is a sheet. */}
      <Dialog open={!twoPane && sidebarOpen} onOpenChange={(o) => !o && toggleSidebar()}>
        <SheetContent bare title="Conversations" side="left" width="w-[18rem]" className="p-0">
          <div className="h-full">
            <Sidebar activeId={activeId} />
          </div>
        </SheetContent>
      </Dialog>

      <main className={cn('flex min-w-0 flex-1 flex-col')}>{children}</main>

      {evidenceInGrid && (
        <>
          <PanelResizer
            side="right"
            width={evidenceWidth}
            onResize={setEvidenceWidth}
            label="Resize the evidence panel"
          />
          <div style={{ width: evidenceWidth }} className="shrink-0">
            <EvidencePanel className="h-full" />
          </div>
        </>
      )}

      <Dialog open={evidenceOverlay} onOpenChange={(o) => !o && toggleEvidence()}>
        <SheetContent
          bare
          title="Evidence"
          side="right"
          width="w-[min(26rem,calc(100vw-2rem))]"
          className="p-0"
        >
          <EvidencePanel className="h-full border-l-0" />
        </SheetContent>
      </Dialog>

      <CommandPalette />
      <UploadDialog />
      <TraceDrawer />
    </div>
  )
}
