import ChatPanel from '@/components/ChatPanel'

export default function Home() {
  return (
    <main className="h-full bg-gray-950 flex items-stretch justify-center">
      <div className="w-full max-w-4xl flex flex-col">
        <ChatPanel />
      </div>
    </main>
  )
}
