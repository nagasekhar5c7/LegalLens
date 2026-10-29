import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { RefusalSuggestion } from '@/api/types'
import { AppShell } from '@/components/shell/AppShell'
import { TopBar } from '@/components/shell/TopBar'
import { ChatPane } from '@/components/chat/ChatPane'
import { PromptBar } from '@/components/composer/PromptBar'
import { useConversations } from '@/store/useConversations'
import { useWorkspace } from '@/store/useWorkspace'
import { useComposer } from '@/store/useComposer'
import { useUi } from '@/store/useUi'

export function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const { current, turns, streamingTurnId, open, startNew, send, stop } = useConversations()
  const documents = useWorkspace((s) => s.documents)
  const { setScope, setMode, setText } = useComposer()
  const setUploadOpen = useUi((s) => s.setUploadOpen)

  useEffect(() => {
    // Read live state rather than subscribing: this effect must fire on route
    // changes only, or it would reload the conversation mid-stream.
    const { streamingTurnId: busy, current: loaded } = useConversations.getState()
    if (busy) return
    if (!conversationId) {
      startNew()
      return
    }
    if (loaded?.id !== conversationId) void open(conversationId)
  }, [conversationId, open, startNew])

  function handleSend() {
    void send(useComposer.getState().text, conversationId, (id) =>
      navigate(`/c/${id}`, { replace: true }),
    )
  }

  /** The refusal card's suggestions have to actually do something. */
  function handleSuggestion(s: RefusalSuggestion) {
    switch (s.kind) {
      case 'widen_scope':
        setScope({ kind: 'workspace', documentIds: [] })
        document.getElementById('prompt')?.focus()
        break
      case 'upload':
        setUploadOpen(true)
        break
      case 'rephrase':
        document.getElementById('prompt')?.focus()
        break
      case 'switch_mode':
        setMode('summary')
        document.getElementById('prompt')?.focus()
        break
    }
  }

  const streaming = streamingTurnId !== null
  const ready = documents.filter((d) => d.status === 'ready').length

  return (
    <AppShell>
      <TopBar
        title={current?.title ?? 'New chat'}
        subtitle={
          turns.length === 0
            ? `${ready} document${ready === 1 ? '' : 's'} searchable`
            : `${turns.filter((t) => t.role === 'assistant').length} responses`
        }
      />

      <ChatPane
        turns={turns}
        documents={documents}
        onSuggestion={handleSuggestion}
        onResend={(text) => {
          setText(text)
          handleSend()
        }}
        onUpload={() => setUploadOpen(true)}
      />

      <PromptBar
        documents={documents}
        streaming={streaming}
        onSend={handleSend}
        onStop={stop}
      />
    </AppShell>
  )
}
