import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, getToken, setToken, type Outbound } from './api'
import Groups from './pages/Groups'
import Incidents from './pages/Incidents'
import Login from './pages/Login'
import Presend from './pages/Presend'
import StaffPage from './pages/Staff'

function useAuthed() {
  const [token, set] = useState(getToken)
  useEffect(() => {
    const h = () => set(getToken())
    window.addEventListener('auth-changed', h)
    return () => window.removeEventListener('auth-changed', h)
  }, [])
  return !!token
}

function Shell() {
  const qc = useQueryClient()
  const { data: waiting = [] } = useQuery({
    queryKey: ['outbound'],
    queryFn: () => api<Outbound[]>('/outbound'),
    select: (rows) => rows.filter((o) => o.status === 'pending' || o.status === 'needs_approval' || o.status === 'failed'),
  })
  const nav: [string, string, number?][] = [
    ['/presend', '预发队列', waiting.length],
    ['/incidents', '事件中心'],
    ['/groups', '群与标记'],
    ['/staff', '我方人员'],
  ]
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="sticky top-0 z-10 border-b bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-4 overflow-x-auto px-4 py-3 text-sm whitespace-nowrap">
          <span className="font-semibold">PlatformLink</span>
          {nav.map(([to, label, n]) => (
            <NavLink key={to} to={to} className={({ isActive }) =>
              isActive ? 'font-medium text-blue-600' : 'text-slate-600 hover:text-slate-900'}>
              {label}
              {!!n && <span className="ml-1 rounded-full bg-red-500 px-1.5 text-xs text-white">{n}</span>}
            </NavLink>
          ))}
          <button className="ml-auto text-slate-500 hover:text-slate-900" onClick={() => { setToken(''); qc.clear() }}>
            退出
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/presend" replace />} />
          <Route path="/presend" element={<Presend />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/staff" element={<StaffPage />} />
          <Route path="*" element={<Navigate to="/presend" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return useAuthed() ? <Shell /> : <Login />
}
