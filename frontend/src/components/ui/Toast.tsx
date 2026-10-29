import { AlertTriangle, Check, Info, X, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useToasts, type Toast as ToastModel } from '@/store/useToasts'
import { IconButton } from './Button'

const ICON: Record<ToastModel['tone'], React.ReactNode> = {
  neutral: <Info className="size-4 text-navy-600" />,
  grounded: <Check className="size-4 text-grounded" />,
  caution: <AlertTriangle className="size-4 text-caution" />,
  danger: <TriangleAlert className="size-4 text-danger" />,
}

const EDGE: Record<ToastModel['tone'], string> = {
  neutral: 'border-l-navy-600',
  grounded: 'border-l-grounded',
  caution: 'border-l-caution',
  danger: 'border-l-danger',
}

/** Toasts are for confirmations only — never for errors the user must act on. */
export function ToastHost() {
  const { toasts, dismiss } = useToasts()
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 left-1/2 z-[60] flex w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            'pointer-events-auto flex items-start gap-2.5 rounded-card border border-l-2 border-line bg-raised px-3 py-2.5 shadow-panel',
            'animate-[ll-rise_170ms_var(--ease-out-quint)]',
            EDGE[t.tone],
          )}
        >
          <span className="mt-px shrink-0">{ICON[t.tone]}</span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-ink">{t.message}</p>
            {t.detail && <p className="mt-0.5 text-2xs leading-relaxed text-ink-2">{t.detail}</p>}
          </div>
          {t.action && (
            <button
              type="button"
              onClick={() => {
                t.action!.run()
                dismiss(t.id)
              }}
              className="shrink-0 rounded-[5px] px-1.5 py-0.5 text-2xs font-semibold text-navy-600 hover:bg-navy-50"
            >
              {t.action.label}
            </button>
          )}
          <IconButton label="Dismiss" size="xs" variant="quiet" onClick={() => dismiss(t.id)}>
            <X className="size-3.5" />
          </IconButton>
        </div>
      ))}
    </div>
  )
}
