import { useEffect, useState } from 'react'

interface Run {
  id: string
  issue_id: string
  provider: string
  status: string
  attempt: number
  started_at: string
}

export function RunList({ onSelectRun }: { onSelectRun: (id: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([])

  useEffect(() => {
    const load = async () => {
      const res = await fetch('/api/runs?active_only=true')
      setRuns(await res.json())
    }
    load()
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [])

  const statusColor = (s: string) => {
    if (s === 'running') return '#38bdf8'
    if (s === 'completed') return '#4ade80'
    if (s === 'failed') return '#f87171'
    return '#94a3b8'
  }

  return (
    <div style={{ padding: '1rem' }}>
      <h2 style={{ marginBottom: '1rem', fontSize: '1.1rem' }}>Runs</h2>
      {runs.length === 0 && <div style={{ color: '#64748b' }}>No active runs</div>}
      {runs.map(run => (
        <div
          key={run.id}
          onClick={() => onSelectRun(run.id)}
          style={{
            padding: '0.75rem', marginBottom: '0.5rem', background: '#1e293b',
            borderRadius: '0.5rem', cursor: 'pointer', border: '1px solid #334155',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 'bold' }}>{run.issue_id}</span>
            <span style={{ color: statusColor(run.status), fontSize: '0.85rem' }}>{run.status}</span>
          </div>
          <div style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.25rem' }}>
            {run.provider} | attempt {run.attempt} | {run.id.slice(0, 12)}
          </div>
        </div>
      ))}
    </div>
  )
}
