'use client'

import { useEffect, useRef } from 'react'
import { Message } from '@/lib/types'
import MessageBubble from './MessageBubble'

interface Props {
  messages: Message[]
}

export default function MessageList({ messages }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="h-full overflow-y-auto flex flex-col gap-3 pr-1">
      {messages.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-gray-600 text-sm">Send a message to get started…</p>
        </div>
      )}
      {messages.map(msg => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
