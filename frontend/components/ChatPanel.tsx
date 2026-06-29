'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { Message, MemoryResult } from '@/lib/types'
import MessageList from './MessageList'
import SuccessCriteria from './SuccessCriteria'
import MemoryPanel from './MemoryPanel'
import InputBar from './InputBar'

function uuid(): string {
  return crypto.randomUUID()
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([])
  const [sessionId, setSessionId] = useState<string>('')
  const [successCriteria, setSuccessCriteria] = useState('')
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [memories, setMemories] = useState<MemoryResult[]>([])

  // Generate sessionId on mount (avoids SSR/client mismatch)
  useEffect(() => {
    setSessionId(uuid())
  }, [])

  const loadMemories = useCallback(async (query: string) => {
    if (!query.trim() || !sessionId) return
    try {
      const res = await fetch(
        `/api/memory?q=${encodeURIComponent(query)}&user_id=${encodeURIComponent(sessionId)}&limit=5`
      )
      if (res.ok) {
        const data: MemoryResult[] = await res.json()
        setMemories(data)
      }
    } catch {
      // silently fail — memory is non-critical
    }
  }, [sessionId])

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || isLoading) return

    const userMsg: Message = { id: uuid(), role: 'user', content: text }
    const assistantId = uuid()
    const assistantMsg: Message = {
      id: assistantId,
      role: 'assistant',
      content: '',
      streaming: true,
    }

    setMessages(prev => [...prev, userMsg, assistantMsg])
    setIsLoading(true)

    const history = messages.map(m => ({ role: m.role, content: m.content }))

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          success_criteria: successCriteria,
          history,
        }),
      })

      if (!res.body) throw new Error('No response body')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data:')) continue
          const raw = trimmed.slice(5).trim()
          if (!raw) continue

          let parsed: { type: string; content: string }
          try {
            parsed = JSON.parse(raw)
          } catch {
            continue
          }

          if (parsed.type === 'token') {
            setMessages(prev =>
              prev.map(m =>
                m.id === assistantId
                  ? { ...m, content: m.content + parsed.content }
                  : m
              )
            )
          } else if (parsed.type === 'done') {
            setMessages(prev =>
              prev.map(m =>
                m.id === assistantId ? { ...m, streaming: false } : m
              )
            )
          } else if (parsed.type === 'error') {
            setMessages(prev =>
              prev.map(m =>
                m.id === assistantId
                  ? { ...m, content: `Error: ${parsed.content}`, streaming: false }
                  : m
              )
            )
          }
        }
      }
    } catch (err) {
      setMessages(prev =>
        prev.map(m =>
          m.id === assistantId
            ? {
                ...m,
                content: `Failed to connect to Specter: ${err instanceof Error ? err.message : String(err)}`,
                streaming: false,
              }
            : m
        )
      )
    } finally {
      setIsLoading(false)
      loadMemories(text)
    }
  }, [messages, sessionId, successCriteria, isLoading, loadMemories])

  const resetSession = useCallback(async () => {
    if (sessionId) {
      try {
        await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'}/api/sessions/${sessionId}`,
          { method: 'DELETE' }
        )
      } catch {
        // ignore — reset client state regardless
      }
    }
    setMessages([])
    setMemories([])
    setSessionId(uuid())
  }, [sessionId])

  const handleSubmit = useCallback(() => {
    sendMessage(input)
    setInput('')
  }, [input, sendMessage])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-gray-800 bg-gray-900 flex-shrink-0">
        <span className="text-lg font-semibold tracking-tight text-white">
          ⚡ Specter
        </span>
        <button
          onClick={resetSession}
          className="text-xs text-gray-400 hover:text-gray-200 transition-colors border border-gray-700 hover:border-gray-500 px-3 py-1.5 rounded-md"
        >
          Reset
        </button>
      </header>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden gap-4 p-4">
        {/* Left: message list */}
        <div className="flex-[2] min-w-0 overflow-hidden">
          <MessageList messages={messages} />
        </div>

        {/* Right: side panel */}
        <aside className="flex-1 min-w-0 flex flex-col gap-4 overflow-y-auto">
          <SuccessCriteria value={successCriteria} onChange={setSuccessCriteria} />
          <MemoryPanel memories={memories} />
        </aside>
      </div>

      {/* Input bar */}
      <InputBar
        value={input}
        onChange={setInput}
        onSubmit={handleSubmit}
        disabled={isLoading}
      />
    </div>
  )
}
