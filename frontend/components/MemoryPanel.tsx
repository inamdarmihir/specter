'use client'

import { MemoryResult } from '@/lib/types'

interface Props {
  memories: MemoryResult[]
}

export default function MemoryPanel({ memories }: Props) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-gray-400 font-medium uppercase tracking-wide">
        🧠 Relevant Memories
      </p>
      {memories.length === 0 ? (
        <p className="text-xs text-gray-600 italic">No memories yet</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {memories.map((mem, i) => {
            const summary = JSON.stringify(mem.value).slice(0, 80)
            return (
              <li
                key={`${mem.key}-${i}`}
                className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="text-gray-300 font-medium truncate">{mem.key}</span>
                  {mem.score !== null && (
                    <span className="flex-shrink-0 text-[10px] bg-violet-900/60 text-violet-300 px-1.5 py-0.5 rounded-full">
                      {Math.round(mem.score * 100)}%
                    </span>
                  )}
                </div>
                <p className="text-gray-500 break-all">{summary}…</p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
