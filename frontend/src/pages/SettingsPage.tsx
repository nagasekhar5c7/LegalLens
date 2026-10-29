import { useEffect, useMemo, useState } from 'react'
import { Keyboard, Lock, LogOut, ReceiptText, ShieldCheck, Trash2, UserRound } from 'lucide-react'
import type { UsageLedger } from '@/api/types'
import { api } from '@/api/client'
import { toApiError } from '@/api/http'
import { formatTokens, formatUsd } from '@/lib/format'
import { SHORTCUTS } from '@/lib/hotkeys'
import { AppShell } from '@/components/shell/AppShell'
import { TopBar } from '@/components/shell/TopBar'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Kbd, Progress, Skeleton } from '@/components/ui/Feedback'
import { DeleteWorkspaceDialog } from '@/components/documents/DeleteDocDialog'
import { useWorkspace } from '@/store/useWorkspace'
import { useSession } from '@/store/useSession'
import { toast } from '@/store/useToasts'

/**
 * The trust surfaces in one place.
 *
 * NFR-10 and TS-6 require the data-handling posture to be documented in terms
 * an attorney can rely on under Rule 1.1 cmt. 8 — so it is stated in the
 * product, in plain language, rather than buried in a policy page.
 */
const POSTURE: { title: string; body: string }[] = [
  {
    title: 'Zero retention at the model provider',
    body: 'Document text is sent to the model provider under zero-retention terms. It is not stored by them and is not used to train any model. This is a contractual property of the deployment, recorded in the governance note — not an assumption.',
  },
  {
    title: 'Encrypted at rest, TLS in transit',
    body: 'Files, extracted text and embeddings are encrypted at rest. Every request between your browser and Legal Lens travels over TLS.',
  },
  {
    title: 'No document content in telemetry',
    body: 'Error tracking and analytics never receive document content. The evaluation set used to test retrieval is built only from public-domain sources — published opinions and publicly filed agreements — never from your documents.',
  },
  {
    title: 'Retrieval and generation are logged',
    body: 'Every retrieval and generation records which documents were searched, which spans were retrieved, the prompt, the model version and the output. That log is what makes an answer reproducible and what satisfies your own record-keeping obligations.',
  },
  {
    title: 'Documents persist until you delete them',
    body: 'There is no automatic expiry. Deletion is a hard delete — file, extracted text, chunks and embeddings — and takes effect immediately for retrieval.',
  },
  {
    title: 'Uploaded documents are untrusted input',
    body: 'Text from your documents is treated as data to reason about, never as instructions to follow. A PDF from opposing counsel cannot redirect the system, invoke a tool, or change its instructions.',
  },
]

export function SettingsPage() {
  const [usage, setUsage] = useState<UsageLedger | null>(null)
  const [usageError, setUsageError] = useState<string | null>(null)
  const [wipeOpen, setWipeOpen] = useState(false)
  const { documents, removeAll } = useWorkspace()
  const me = useSession((s) => s.me)
  const signOut = useSession((s) => s.signOut)

  /* NFR-8: the totals are the server's, computed over every transaction rather
     than over whatever rows this page happens to hold. */
  useEffect(() => {
    void api
      .getUsage()
      .then(setUsage)
      .catch((thrown) => setUsageError(toApiError(thrown).message))
  }, [])

  const grouped = useMemo(() => {
    const map = new Map<string, typeof SHORTCUTS>()
    for (const s of SHORTCUTS) map.set(s.group, [...(map.get(s.group) ?? []), s])
    return [...map.entries()]
  }, [])

  return (
    <AppShell showEvidence={false}>
      <TopBar title="Settings" subtitle="Data & privacy · usage · keyboard" />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-8 px-4 py-6 sm:px-6">
          <section>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ShieldCheck aria-hidden className="size-4 text-grounded" />
              Data & privacy
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Every document you add is treated as carrying client confidences and privileged
              material. These are the properties of the deployment you can rely on.
            </p>
            <dl className="mt-3 divide-y divide-line rounded-card border border-line bg-raised">
              {POSTURE.map((p) => (
                <div key={p.title} className="px-3.5 py-3">
                  <dt className="text-xs font-semibold text-ink">{p.title}</dt>
                  <dd className="mt-1 text-2xs leading-relaxed text-ink-2">{p.body}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ReceiptText aria-hidden className="size-4 text-ink-3" />
              Usage and cost
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Cost is attributed per request. The target is $0.05 per question and $0.50 per
              document ingested.
            </p>

            {usageError ? (
              <p className="mt-3 rounded-card border border-line bg-raised px-3.5 py-3 text-2xs text-ink-2">
                {usageError}
              </p>
            ) : !usage ? (
              <Skeleton className="mt-3 h-32 w-full" />
            ) : (
              <>
                {(
                  <div className="mt-3 grid gap-2 sm:grid-cols-3">
                    {[
                      ['Total spend', formatUsd(usage.totalUsd)],
                      ['Questions answered', String(usage.totalAsks)],
                      ['Tokens', formatTokens(usage.totalTokens)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-card border border-line bg-raised px-3 py-2.5">
                        <p className="text-[10px] text-ink-3">{label}</p>
                        <p className="mt-0.5 font-mono text-sm font-medium text-ink">{value}</p>
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-2 overflow-hidden rounded-card border border-line bg-raised">
                  <table className="w-full border-collapse text-xs">
                    <thead className="border-b border-line bg-canvas">
                      <tr>
                        <th scope="col" className="px-3 py-2 text-left text-2xs uppercase tracking-wider text-ink-3">
                          Conversation
                        </th>
                        <th scope="col" className="px-3 py-2 text-right text-2xs uppercase tracking-wider text-ink-3">
                          Responses
                        </th>
                        <th scope="col" className="px-3 py-2 text-right text-2xs uppercase tracking-wider text-ink-3">
                          Cost
                        </th>
                        <th scope="col" className="hidden px-3 py-2 text-left text-2xs uppercase tracking-wider text-ink-3 sm:table-cell">
                          Against {formatUsd(usage.budgetPerAskUsd)} / ask
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {usage.rows.length === 0 && (
                        <tr>
                          <td colSpan={4} className="px-3 py-4 text-center text-2xs text-ink-3">
                            No questions asked yet — nothing has been spent.
                          </td>
                        </tr>
                      )}
                      {usage.rows.map((r) => {
                        const perAsk = r.asks > 0 ? r.usd / r.asks : 0
                        return (
                          <tr key={r.conversationId} className="border-b border-line last:border-0">
                            <td className="max-w-0 px-3 py-2">
                              <span className="block truncate text-ink">{r.title}</span>
                            </td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-ink-2">
                              {r.asks}
                            </td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-ink-2">
                              {formatUsd(r.usd)}
                            </td>
                            <td className="hidden px-3 py-2 sm:table-cell">
                              <div className="flex items-center gap-2">
                                <Progress
                                  value={(perAsk / usage.budgetPerAskUsd) * 100}
                                  tone={perAsk <= usage.budgetPerAskUsd ? 'grounded' : 'caution'}
                                  className="w-24"
                                  label={`${formatUsd(perAsk)} per response`}
                                />
                                <span className="font-mono text-[10px] text-ink-3">
                                  {formatUsd(perAsk)}
                                </span>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>

          <section>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <UserRound aria-hidden className="size-4 text-ink-3" />
              Account
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Signing out revokes the session on the server, not just in this browser.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3 rounded-card border border-line bg-raised px-3.5 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-ink">{me?.email ?? '—'}</p>
                <p className="truncate text-2xs text-ink-3">{me?.workspaceName ?? 'Workspace'}</p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  void signOut().then(() => toast.done('Signed out'))
                }}
              >
                <LogOut aria-hidden className="size-3.5" />
                Sign out
              </Button>
            </div>
          </section>

          <section>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Keyboard aria-hidden className="size-4 text-ink-3" />
              Keyboard
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Everything in Legal Lens is reachable without a mouse, including citation
              verification.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {grouped.map(([group, items]) => (
                <div key={group} className="rounded-card border border-line bg-raised p-3">
                  <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-3">
                    {group}
                  </h3>
                  <dl className="mt-2 space-y-1.5">
                    {items.map((s) => (
                      <div key={s.label} className="flex items-baseline gap-2">
                        <dt className="min-w-0 flex-1 text-2xs text-ink-2">{s.label}</dt>
                        <dd className="shrink-0">
                          <Kbd>{s.keys}</Kbd>
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Lock aria-hidden className="size-4 text-ink-3" />
              Account
            </h2>
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-card border border-line bg-raised px-3.5 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium text-ink">dana@vancelaw.example</p>
                <p className="mt-0.5 text-2xs text-ink-3">
                  Single-user workspace. Roles, shared matters and per-matter access control are
                  Phase 2.
                </p>
              </div>
              <Badge tone="neutral">v0.1.0 · UI phase</Badge>
            </div>
          </section>

          <section className="rounded-card border border-danger-line bg-danger-bg px-3.5 py-3">
            <h2 className="text-xs font-semibold text-danger">Delete everything</h2>
            <p className="mt-1 text-2xs leading-relaxed text-ink-2">
              Removes every document, its extracted text, chunks and embeddings, and all
              conversation history. This cannot be undone.
            </p>
            <Button size="sm" variant="danger" className="mt-2.5" onClick={() => setWipeOpen(true)}>
              <Trash2 aria-hidden className="size-3.5" />
              Delete workspace
            </Button>
          </section>
        </div>
      </div>

      <DeleteWorkspaceDialog
        open={wipeOpen}
        onOpenChange={setWipeOpen}
        documentCount={documents.length}
        onConfirm={() => void removeAll()}
      />
    </AppShell>
  )
}
