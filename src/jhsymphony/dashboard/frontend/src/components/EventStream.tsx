import { useEffect, useRef, useState } from 'react'

interface Event {
  seq: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export function EventStream({ runId }: { runId: string | null }) {
  const [events, setEvents] = useState<Event[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!runId) return
    setEvents([])
    const load = async () => {
      const res = await fetch(`/api/runs/${runId}/events`)
      setEvents(await res.json())
    }
    load()
    const id = setInterval(load, 2000)
    return () => clearInterval(id)
  }, [runId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  if (!runId) {
    return <div style={{ padding: '2rem', color: '#64748b' }}>Select a run to view events</div>
  }

  const typeColor = (t: string) => {
    if (t === 'message.delta') return '#e2e8f0'
    if (t === 'tool.call') return '#fbbf24'
    if (t === 'tool.result') return '#a78bfa'
    if (t === 'usage') return '#38bdf8'
    if (t === 'error') return '#f87171'
    if (t === 'completed') return '#4ade80'
    return '#94a3b8'
  }

  return (
    <div style={{ padding: '1rem', height: '100%', overflowY: 'auto' }}>
      <h2 style={{ marginBottom: '1rem', fontSize: '1.1rem' }}>Events — {runId.slice(0, 12)}</h2>
      {events.map(evt => (
        <div key={evt.seq} style={{ marginBottom: '0.5rem', fontFamily: 'monospace', fontSize: '0.85rem' }}>
          <span style={{ color: '#64748b' }}>{String(evt.seq).padStart(4, ' ')} </span>
          <span style={{ color: typeColor(evt.type), fontWeight: 'bold' }}>[{evt.type}]</span>{' '}
          <span style={{ color: '#cbd5e1' }}>
            {evt.type === 'message.delta'
              ? String(evt.payload.text || '')
              : JSON.stringify(evt.payload)}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
