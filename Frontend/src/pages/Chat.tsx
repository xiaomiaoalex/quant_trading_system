import { useState, useCallback, useRef, useEffect } from 'react'
import { clsx } from 'clsx'
import { LoadingState, ErrorState, EmptyState, ConfirmDialog } from '@/components/ui'
import { useChatSessions, useChatHistory, useChatSession, useSendMessage, useCreateSession, useDeleteSession, useApproveStrategy, useRejectStrategy } from '@/hooks'
import { formatAPIError } from '@/api/client'
import type { ChatSession, ChatMessage, SessionStatus } from '@/types'
import { SESSION_STATUS_DISPLAY, MESSAGE_ROLE_DISPLAY } from '@/types'

function SessionListItem({ session, isSelected, onSelect, onDelete }: {
  session: ChatSession
  isSelected: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const statusConfig = SESSION_STATUS_DISPLAY[session.status as SessionStatus] ?? { label: session.status, color: 'text-gray-400' }

  return (
    <div
      className={clsx(
        'flex items-center justify-between p-3 rounded-lg cursor-pointer transition-colors',
        isSelected ? 'bg-blue-900/30 border border-blue-700/50' : 'hover:bg-gray-800/50 border border-transparent'
      )}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-200 truncate">{session.session_id.slice(0, 8)}...</span>
          <span className={clsx('text-xs', statusConfig.color)}>{statusConfig.label}</span>
        </div>
        <p className="text-xs text-gray-500 mt-0.5">
          {session.message_count} messages · {new Date(session.created_at).toLocaleDateString()}
        </p>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="p-1.5 rounded hover:bg-red-900/30 text-gray-500 hover:text-red-400 transition-colors"
        title="Delete session"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const roleConfig = MESSAGE_ROLE_DISPLAY[message.role] ?? { label: message.role, color: 'text-gray-400' }
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div className={clsx(
        'max-w-[70%] rounded-lg px-4 py-2.5',
        isUser ? 'bg-blue-900/40' : isSystem ? 'bg-gray-800/50' : 'bg-purple-900/40'
      )}>
        <div className="flex items-center gap-2 mb-1">
          <span className={clsx('text-xs font-medium', roleConfig.color)}>{roleConfig.label}</span>
          <span className="text-xs text-gray-500">{new Date(message.timestamp).toLocaleTimeString()}</span>
        </div>
        <p className="text-sm text-gray-200 whitespace-pre-wrap">{message.content}</p>
        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-2 pt-2 border-t border-gray-700/50">
            {message.attachments.map(att => (
              <div key={att.attachment_id} className="flex items-center gap-2 text-xs text-gray-400">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <span>{att.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function Chat() {
  const { data: sessions, isLoading, isError, error, refetch } = useChatSessions()
  const { createSession, isPending: isCreating } = useCreateSession()
  const { deleteSession } = useDeleteSession()
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [showNewSessionConfirm, setShowNewSessionConfirm] = useState(false)
  const [inputMessage, setInputMessage] = useState('')
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  const { data: messages, refetch: refetchMessages } = useChatHistory(selectedSessionId ?? '')
  const { data: currentSession } = useChatSession(selectedSessionId ?? '')
  const { sendMessage, isPending: isSending, error: sendError } = useSendMessage(selectedSessionId ?? '')
  const { approve, isPending: isApproving } = useApproveStrategy(selectedSessionId ?? '')
  const { reject, isPending: isRejecting } = useRejectStrategy(selectedSessionId ?? '')

  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    if (messages && messages.length > 0) {
      scrollToBottom()
    }
  }, [messages, scrollToBottom])

  const handleCreateSession = useCallback(async () => {
    const session = await createSession()
    if (session) {
      setSelectedSessionId(session.session_id)
      setShowNewSessionConfirm(false)
    }
  }, [createSession])

  const handleSendMessage = useCallback(async () => {
    if (!inputMessage.trim() || !selectedSessionId) return
    const result = await sendMessage(inputMessage)
    if (result) {
      setInputMessage('')
      refetchMessages()
    }
  }, [inputMessage, selectedSessionId, sendMessage, refetchMessages])

  const handleApprove = useCallback(async () => {
    const result = await approve()
    if (result?.success) {
      setSuccessMsg(`Strategy approved and registered: ${result.strategy_id}`)
      setTimeout(() => setSuccessMsg(null), 5000)
    }
  }, [approve])

  const handleReject = useCallback(async () => {
    const success = await reject()
    if (success) {
      setSuccessMsg('Strategy rejected')
      setTimeout(() => setSuccessMsg(null), 3000)
    }
  }, [reject])

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    const success = await deleteSession(sessionId)
    if (success && selectedSessionId === sessionId) {
      setSelectedSessionId(null)
    }
  }, [deleteSession, selectedSessionId])

  const isWaitingApproval = currentSession?.status === 'waiting_approval'

  if (isLoading) return <div className="p-6"><LoadingState message="Loading chat sessions..." /></div>
  if (isError) return <div className="p-6"><ErrorState title="Failed to load chat sessions" message={formatAPIError(error)} onRetry={refetch} /></div>

  return (
    <div className="min-h-screen bg-gray-900 flex">
      <aside className="w-72 border-r border-gray-800 flex flex-col">
        <div className="p-4 border-b border-gray-800">
          <button
            onClick={() => setShowNewSessionConfirm(true)}
            disabled={isCreating}
            className="w-full rounded-md bg-blue-900/30 px-4 py-2 text-sm font-medium text-blue-300 hover:bg-blue-900/50 disabled:opacity-50 transition-colors"
          >
            {isCreating ? 'Creating...' : 'New Chat Session'}
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {(!sessions || sessions.length === 0) ? (
            <p className="text-sm text-gray-500 text-center py-8">No chat sessions yet</p>
          ) : (
            sessions.map(session => (
              <SessionListItem
                key={session.session_id}
                session={session}
                isSelected={selectedSessionId === session.session_id}
                onSelect={() => setSelectedSessionId(session.session_id)}
                onDelete={() => handleDeleteSession(session.session_id)}
              />
            ))
          )}
        </div>
      </aside>

      <main className="flex-1 flex flex-col">
        {!selectedSessionId ? (
          <div className="flex-1 flex items-center justify-center">
            <EmptyState
              title="No Session Selected"
              message="Select a chat session from the sidebar or create a new one."
              action={{ label: 'New Session', onClick: () => setShowNewSessionConfirm(true) }}
            />
          </div>
        ) : (
          <>
            <header className="px-6 py-4 border-b border-gray-800 bg-gray-900/80">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-white">AI Strategy Chat</h2>
                  <p className="text-xs text-gray-500">
                    Session: {selectedSessionId.slice(0, 8)}... · {messages?.length ?? 0} messages
                  </p>
                </div>
                {isWaitingApproval && (
                  <div className="flex gap-2">
                    <button
                      onClick={handleApprove}
                      disabled={isApproving}
                      className="rounded-md bg-green-900/30 px-3 py-1.5 text-sm font-medium text-green-300 hover:bg-green-900/50 disabled:opacity-50"
                    >
                      {isApproving ? 'Approving...' : 'Approve Strategy'}
                    </button>
                    <button
                      onClick={handleReject}
                      disabled={isRejecting}
                      className="rounded-md bg-red-900/30 px-3 py-1.5 text-sm font-medium text-red-300 hover:bg-red-900/50 disabled:opacity-50"
                    >
                      {isRejecting ? 'Rejecting...' : 'Reject'}
                    </button>
                  </div>
                )}
              </div>
            </header>

            <div className="flex-1 overflow-y-auto p-6 space-y-4">
              {successMsg && (
                <div className="rounded-lg border border-green-900/50 bg-green-950/20 px-4 py-2">
                  <p className="text-sm text-green-400">{successMsg}</p>
                </div>
              )}
              {sendError && (
                <div className="rounded-lg border border-red-900/50 bg-red-950/20 px-4 py-2">
                  <p className="text-sm text-red-400">Failed to send: {sendError}</p>
                </div>
              )}
              {messages && messages.map(msg => (
                <MessageBubble key={msg.message_id} message={msg} />
              ))}
              <div ref={messagesEndRef} />
            </div>

            <footer className="p-4 border-t border-gray-800">
              <div className="flex gap-3">
                <input
                  type="text"
                  value={inputMessage}
                  onChange={e => setInputMessage(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSendMessage()}
                  placeholder="Describe your trading strategy..."
                  disabled={isSending}
                  className="flex-1 rounded-lg bg-gray-800 border border-gray-700 px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                />
                <button
                  onClick={handleSendMessage}
                  disabled={isSending || !inputMessage.trim()}
                  className="rounded-lg bg-blue-900/30 px-4 py-2.5 text-sm font-medium text-blue-300 hover:bg-blue-900/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {isSending ? 'Sending...' : 'Send'}
                </button>
              </div>
            </footer>
          </>
        )}
      </main>

      <ConfirmDialog
        isOpen={showNewSessionConfirm}
        title="New Chat Session"
        message="Create a new AI strategy chat session?"
        confirmLabel="Create"
        cancelLabel="Cancel"
        isLoading={isCreating}
        onConfirm={handleCreateSession}
        onCancel={() => setShowNewSessionConfirm(false)}
      />
    </div>
  )
}
