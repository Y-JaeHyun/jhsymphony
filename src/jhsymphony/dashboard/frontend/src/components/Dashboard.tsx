import { useEffect, useRef, useState } from 'react'
import { StatsBar } from './StatsBar'
import { RunList } from './RunList'
import { EventStream } from './EventStream'

export function Dashboard() {
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/events`)
    wsRef.current = ws
    ws.onmessage = (msg) => {
      console.log('WS event:', msg.data)
    }
    return () => ws.close()
  }, [])

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '1rem 2rem', background: '#0f172a', borderBottom: '1px solid #334155' }}>
        <h1 style={{ fontSize: '1.3rem', fontWeight: 'bold' }}>JHSymphony</h1>
      </div>
      <StatsBar />
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        <div style={{ width: '350px', borderRight: '1px solid #334155', overflowY: 'auto' }}>
          <RunList onSelectRun={setSelectedRun} />
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <EventStream runId={selectedRun} />
        </div>
      </div>
    </div>
  )
}
