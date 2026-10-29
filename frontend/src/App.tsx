import { Suspense, lazy } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/Floating'
import { ToastHost } from '@/components/ui/Toast'
import { Spinner } from '@/components/ui/Feedback'
import { RequireAuth } from '@/components/auth/RequireAuth'
import { ChatPage } from '@/pages/ChatPage'

/* The chat workspace is the entry point and ships in the main chunk. The rest
   are split — the viewer in particular pulls weight nobody needs to download
   before asking their first question. */
const LibraryPage = lazy(() => import('@/pages/LibraryPage').then((m) => ({ default: m.LibraryPage })))
const DocumentPage = lazy(() => import('@/pages/DocumentPage').then((m) => ({ default: m.DocumentPage })))
const SettingsPage = lazy(() => import('@/pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const LoginPage = lazy(() => import('@/pages/LoginPage').then((m) => ({ default: m.LoginPage })))
const NotFoundPage = lazy(() => import('@/pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })))
const KitchenSinkPage = lazy(() =>
  import('@/pages/KitchenSinkPage').then((m) => ({ default: m.KitchenSinkPage })),
)

function RouteFallback() {
  return (
    <div className="grid min-h-dvh place-items-center bg-canvas" role="status" aria-live="polite">
      <span className="flex items-center gap-2 text-xs text-ink-3">
        <Spinner className="size-4 text-navy-600" />
        Loading…
      </span>
    </div>
  )
}

export default function App() {
  return (
    <TooltipProvider delayDuration={320} skipDelayDuration={240}>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          {/* Public: the sign-in form and the component gallery. Everything
              else needs the session cookie, because every endpoint behind it
              does. */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/kitchen-sink" element={<KitchenSinkPage />} />

          <Route
            element={
              <RequireAuth>
                <Outlet />
              </RequireAuth>
            }
          >
            <Route path="/" element={<ChatPage />} />
            <Route path="/c/:conversationId" element={<ChatPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/doc/:documentId" element={<DocumentPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>

          <Route path="/404" element={<NotFoundPage />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Routes>
      </Suspense>
      <ToastHost />
    </TooltipProvider>
  )
}
