import { useEffect, useState } from 'react'

interface Stats {
  active_runs: number
  daily_cost: number
}

export function StatsBar() {
  const [stats, setStats] = useState<Stats>({ active_runs: 0, daily_cost: 0 })

  useEffect(() => {
    const load = async () => {
      const res = await fetch('/api/stats')
      setStats(await res.json())
    }
    load()
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ display: 'flex', gap: '2rem', padding: '1rem 2rem', background: '#1e293b', borderBottom: '1px solid #334155' }}>
      <div>
        <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Active Runs</div>
        <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: '#38bdf8' }}>{stats.active_runs}</div>
      </div>
      <div>
        <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Daily Cost</div>
        <div style={{ fontSize: '1.5rem', fontWeight: 'bold', color: stats.daily_cost > 40 ? '#f87171' : '#4ade80' }}>
          ${stats.daily_cost.toFixed(2)}
        </div>
      </div>
    </div>
  )
}
