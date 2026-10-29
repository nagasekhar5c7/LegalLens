import { Link } from 'react-router-dom'
import { FileUp, PanelLeftOpen, PanelRightOpen, Scale, Search } from 'lucide-react'
import { MOD } from '@/lib/hotkeys'
import { Button, IconButton } from '@/components/ui/Button'
import { Kbd } from '@/components/ui/Feedback'
import { Tooltip } from '@/components/ui/Floating'
import { useUi } from '@/store/useUi'
import { DemoMenu } from './DemoMenu'

export function TopBar({ title, subtitle }: { title?: string; subtitle?: React.ReactNode }) {
  const { sidebarOpen, toggleSidebar, evidenceOpen, toggleEvidence, setPaletteOpen, setUploadOpen } =
    useUi()

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-canvas px-3 print:hidden">
      {!sidebarOpen && (
        <>
          <Tooltip content="Show the sidebar" shortcut="[">
            <IconButton label="Show the sidebar" size="sm" variant="quiet" onClick={toggleSidebar}>
              <PanelLeftOpen className="size-4" />
            </IconButton>
          </Tooltip>
          <Link to="/" className="flex shrink-0 items-center gap-1.5 lg:hidden">
            <span aria-hidden className="grid size-5 place-items-center rounded-[5px] bg-navy-600">
              <Scale className="size-3 text-ink-inverse" />
            </span>
          </Link>
        </>
      )}

      <div className="min-w-0 flex-1">
        {title && (
          <h1 className="truncate text-xs font-semibold text-ink">{title}</h1>
        )}
        {subtitle && <div className="truncate text-2xs text-ink-3">{subtitle}</div>}
      </div>

      <Button
        size="xs"
        variant="quiet"
        className="hidden gap-2 text-ink-3 sm:flex"
        onClick={() => setPaletteOpen(true)}
      >
        <Search aria-hidden className="size-3.5" />
        Search
        <Kbd>{MOD}K</Kbd>
      </Button>

      <DemoMenu />

      <Tooltip content="Upload documents" shortcut={`${MOD} U`}>
        <IconButton
          label="Upload documents"
          size="sm"
          variant="quiet"
          onClick={() => setUploadOpen(true)}
        >
          <FileUp className="size-4" />
        </IconButton>
      </Tooltip>

      {!evidenceOpen && (
        <Tooltip content="Show the evidence panel" shortcut="]">
          <IconButton
            label="Show the evidence panel"
            size="sm"
            variant="quiet"
            onClick={toggleEvidence}
          >
            <PanelRightOpen className="size-4" />
          </IconButton>
        </Tooltip>
      )}
    </header>
  )
}
