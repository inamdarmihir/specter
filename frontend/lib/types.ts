export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}

export interface MemoryResult {
  key: string
  value: Record<string, unknown>
  score: number | null
}

export interface ChatRequest {
  session_id: string
  message: string
  success_criteria: string
  history: Array<{ role: string; content: string }>
}
