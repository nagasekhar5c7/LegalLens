import { useEffect, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Eye, EyeOff, Lock, Scale, ShieldCheck } from 'lucide-react'
import { toApiError } from '@/api/http'
import { Button, IconButton } from '@/components/ui/Button'
import { Field, Input } from '@/components/ui/Field'
import { useSession } from '@/store/useSession'

/** FR-27: one user, email and password, no roles in v1. */
export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const status = useSession((s) => s.status)
  const lapsed = useSession((s) => s.lapsed)
  const bootstrap = useSession((s) => s.bootstrap)
  const signIn = useSession((s) => s.signIn)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /* An already-valid cookie should not be met with a login form. This route is
     public, so nothing else would have probed. */
  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  /** Back to wherever `RequireAuth` bounced from, or the chat. */
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await signIn(email, password)
      if (res.ok) navigate(from, { replace: true })
      else setError(res.error ?? 'That email and password do not match an account.')
    } catch (thrown) {
      setError(toApiError(thrown).message)
    } finally {
      setBusy(false)
    }
  }

  if (status === 'authed') return <Navigate to={from} replace />

  return (
    <div className="grid min-h-dvh place-items-center bg-canvas px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="text-center">
          <span
            aria-hidden
            className="mx-auto grid size-10 place-items-center rounded-panel bg-navy-600 shadow-sm"
          >
            <Scale className="size-5 text-ink-inverse" />
          </span>
          <h1 className="mt-4 text-lg font-semibold tracking-[-0.015em] text-ink">Legal Lens</h1>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
            Question, summarise and draft from your own documents — with a citation behind every
            sentence.
          </p>
        </div>

        <form
          onSubmit={submit}
          className="mt-6 space-y-4 rounded-panel border border-line bg-raised p-5 shadow-sm"
        >
          <Field label="Email" required>
            {(p) => (
              <Input
                {...p}
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@firm.example"
              />
            )}
          </Field>

          {lapsed && !error && (
            <p
              role="status"
              className="rounded-card border border-caution-line bg-caution-bg px-3 py-2 text-2xs leading-relaxed text-caution"
            >
              {lapsed}
            </p>
          )}

          <Field label="Password" required error={error}>
            {(p) => (
              <div className="relative">
                <Input
                  {...p}
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 8 characters"
                  className="pr-9"
                />
                <IconButton
                  label={show ? 'Hide password' : 'Show password'}
                  size="xs"
                  variant="quiet"
                  type="button"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2"
                  onClick={() => setShow((v) => !v)}
                >
                  {show ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                </IconButton>
              </div>
            )}
          </Field>

          <Button type="submit" variant="primary" size="md" className="w-full" loading={busy}>
            <Lock aria-hidden className="size-3.5" />
            Sign in
          </Button>

          <p className="text-center text-[10px] text-ink-3">
            The development workspace is seeded by <code className="font-mono">make seed</code> as{' '}
            <span className="font-mono">dana@example.com</span>.
          </p>
        </form>

        <div className="mt-4 flex items-start gap-2 rounded-card border border-grounded-line bg-grounded-bg px-3 py-2.5">
          <ShieldCheck aria-hidden className="mt-px size-3.5 shrink-0 text-grounded" />
          <p className="text-[10px] leading-relaxed text-grounded">
            Uploaded documents are treated as carrying client confidences: encrypted at rest, TLS in
            transit, and sent only to a model provider under zero-retention terms with no training on
            your content.
          </p>
        </div>
      </div>
    </div>
  )
}
