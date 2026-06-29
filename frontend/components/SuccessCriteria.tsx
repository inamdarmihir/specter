'use client'

interface Props {
  value: string
  onChange: (v: string) => void
}

export default function SuccessCriteria({ value, onChange }: Props) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-gray-400 font-medium uppercase tracking-wide">
        Success Criteria
      </label>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="What does success look like?"
        rows={4}
        className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100
                   placeholder-gray-600 resize-none focus:outline-none focus:border-violet-500
                   focus:ring-1 focus:ring-violet-500 transition-colors"
      />
    </div>
  )
}
