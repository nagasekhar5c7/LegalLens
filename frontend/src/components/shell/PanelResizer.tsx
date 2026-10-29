import { useCallback, useRef } from 'react'
import { cn } from '@/lib/cn'

/**
 * Keyboard-operable drag handle. A mouse-only resizer would fail NFR-13, and
 * arrow-key resizing is genuinely useful for fine adjustment.
 */
export function PanelResizer({
  side,
  width,
  onResize,
  label,
}: {
  side: 'left' | 'right'
  width: number
  onResize: (w: number) => void
  label: string
}) {
  const dragging = useRef(false)

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      dragging.current = true
      const startX = e.clientX
      const startWidth = width
      const el = e.currentTarget
      el.setPointerCapture(e.pointerId)

      function move(ev: PointerEvent) {
        if (!dragging.current) return
        const delta = side === 'left' ? ev.clientX - startX : startX - ev.clientX
        onResize(startWidth + delta)
      }
      function up() {
        dragging.current = false
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
        document.body.style.cursor = ''
      }
      document.body.style.cursor = 'col-resize'
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
    },
    [side, width, onResize],
  )

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 32 : 8
        if (e.key === 'ArrowLeft') {
          e.preventDefault()
          onResize(width + (side === 'left' ? -step : step))
        } else if (e.key === 'ArrowRight') {
          e.preventDefault()
          onResize(width + (side === 'left' ? step : -step))
        }
      }}
      className={cn(
        'group relative z-10 w-1 shrink-0 cursor-col-resize touch-none bg-transparent',
        'before:absolute before:inset-y-0 before:-left-1 before:-right-1 before:content-[""]',
        'hover:bg-navy-300 focus-visible:bg-navy-600',
      )}
    />
  )
}
