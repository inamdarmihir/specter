import { NextRequest, NextResponse } from 'next/server'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const params = searchParams.toString()
  const upstream = await fetch(
    `${API}/api/memory/search${params ? `?${params}` : ''}`,
    { method: 'GET', headers: { 'Content-Type': 'application/json' } }
  )
  const data = await upstream.json()
  return NextResponse.json(data, { status: upstream.status })
}
