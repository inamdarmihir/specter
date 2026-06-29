'use client'

import { useRef, useEffect, KeyboardEvent } from 'react'
import clsx from 'clsx'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  disabled: boolean
}

export default function InputBar({ value, onChange, onSubmit, disabled }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize up to 4 rows
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const lineHeight = 24 // px, matches text-sm leading
    const maxHeight = lineHeight * 4 + 16 // 4 rows + padding
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  }, [value])

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!disabled && value.trim()) onSubmit()
    }
  }

  const canSend = !disabled && value.trim().length > 0

  return (
    <div className="flex-shrink-0 bg-gray-900 border-t border-gray-800 px-4 py-3">
      <div className="flex items-end gap-3">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Message Specter… (Shift+Enter for newline)"
          disabled={disabled}
          rows={1}
          className={clsx(
            'flex-1 bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm text-gray-100',
            'placeholder-gray-600 resize-none overflow-y-auto leading-6',
            'focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500',
            'transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
          )}
        />
        <button
          onClick={onSubmit}
          disabled={!canSend}
          className={clsx(
            'flex-shrink-0 px-4 py-2.5 rounded-xl text-sm font-medium transition-colors',
            canSend
              ? 'bg-violet-600 hover:bg-violet-500 text-white'
              : 'bg-gray-700 text-gray-500 cursor-not-allowed'
          )}
        >
          {disabled ? (
            <span className="flex items-center gap-1.5">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              Thinking
            </span>
          ) : (
            'Send'
          )}
        </button>
      </div>
    </div>
  )
}
