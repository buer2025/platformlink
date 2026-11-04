import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Groups from './pages/Groups'
import Incidents from './pages/Incidents'
import Presend from './pages/Presend'

const nav = [
  ['/presend', '预发队列'],
  ['/incidents', '事件中心'],
  ['/groups', '群与标记'],
]

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
          <span className="font-semibold">PlatformLink</span>
          {nav.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                isActive ? 'text-blue-600 font-medium' : 'text-slate-600 hover:text-slate-900'
              }
            >
              {label}
            </NavLink>
          ))}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Navigate to="/presend" />} />
          <Route path="/presend" element={<Presend />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/groups" element={<Groups />} />
        </Routes>
      </main>
    </div>
  )
}
