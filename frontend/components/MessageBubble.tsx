'use client'

import { Message } from '@/lib/types'
import clsx from 'clsx'

interface Props {
  message: Message
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={clsx(
          'max-w-[80%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed',
          'whitespace-pre-wrap break-words',
          isUser
            ? 'bg-violet-600 text-white rounded-br-sm'
            : 'bg-gray-800 text-gray-100 rounded-bl-sm'
        )}
      >
        {message.content}
        {message.streaming && (
          <span className="inline-block animate-pulse ml-0.5 text-gray-400">▌</span>
        )}
        {message.streaming && message.content === '' && (
          <span className="inline-flex gap-1">
            <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 bg-gray-500 rounded-full animate-bounce [animation-delay:300ms]" />
          </span>
        )}
      </div>
    </div>
  )
}
