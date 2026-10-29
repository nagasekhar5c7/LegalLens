import { create } from 'zustand'

export interface Toast {
  id: string
  message: string
  tone: 'neutral' | 'grounded' | 'danger' | 'caution'
  detail?: string
  action?: { label: string; run: () => void }
}

interface ToastState {
  toasts: Toast[]
  push: (t: Omit<Toast, 'id'> & { id?: string }) => void
  dismiss: (id: string) => void
}

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: ({ id, ...t }) => {
    const toast: Toast = { id: id ?? `t_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`, ...t }
    set((s) => ({ toasts: [...s.toasts.slice(-3), toast] }))
    window.setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((x) => x.id !== toast.id) }))
    }, t.action ? 8000 : 4500)
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

/** Convenience wrappers so call sites read as prose. */
export const toast = {
  info: (message: string, detail?: string) =>
    useToasts.getState().push({ message, detail, tone: 'neutral' }),
  done: (message: string, detail?: string) =>
    useToasts.getState().push({ message, detail, tone: 'grounded' }),
  warn: (message: string, detail?: string) =>
    useToasts.getState().push({ message, detail, tone: 'caution' }),
  error: (message: string, detail?: string) =>
    useToasts.getState().push({ message, detail, tone: 'danger' }),
  action: (message: string, action: Toast['action'], detail?: string) =>
    useToasts.getState().push({ message, detail, tone: 'neutral', action }),
}
