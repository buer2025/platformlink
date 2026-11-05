import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Command } from 'cmdk'
import {
  BarChart3, BookOpen, Inbox, LogOut, Monitor, Moon, Search, Sun, Users, Workflow as WorkflowIcon, Zap, MessagesSquare,
  Keyboard,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { api, getToken, setToken, type Group, type Incident, type Outbound } from './api'
import { useHotkeys, useTheme } from './hooks'
import Login from './pages/Login'
import Presend from './pages/Presend'
const Workflows = lazy(() => import('./pages/Workflows'))
const StatsPage = lazy(() => import('./pages/Stats'))
const StaffPage = lazy(() => import('./pages/Staff'))
const KnowledgePage = lazy(() => import('./pages/Knowledge'))
const Incidents = lazy(() => import('./pages/Incidents'))
const Groups = lazy(() => import('./pages/Groups'))
import { Kbd, Skeleton } from './ui'

function useAuthed() {
  const [token, set] = useState(getToken)
  useEffect(() => {
    const h = () => set(getToken())
    window.addEventListener('auth-changed', h)
    return () => window.removeEventListener('auth-changed', h)
  }, [])
  return !!token
}

const NAV: { to: string; label: string; icon: ReactNode; key: string }[] = [
  { to: '/presend', label: '预发队列', icon: <Inbox size={16} />, key: 'p' },
  { to: '/incidents', label: '事件中心', icon: <Zap size={16} />, key: 'i' },
  { to: '/workflows', label: '处理链路', icon: <WorkflowIcon size={16} />, key: 'w' },
  { to: '/knowledge', label: '知识库', icon: <BookOpen size={16} />, key: 'k' },
  { to: '/groups', label: '群与标记', icon: <MessagesSquare size={16} />, key: 'g' },
  { to: '/staff', label: '我方人员', icon: <Users size={16} />, key: 's' },
  { to: '/stats', label: '数据看板', icon: <BarChart3 size={16} />, key: 'd' },
]

const SHORTCUTS: [string, string[]][] = [
  ['打开命令面板', ['⌘', 'K']],
  ['跳转页面', ['G', '然后 P / I / W / K / G / S / D']],
  ['上下移动', ['J', 'K']],
  ['打开详情', ['Enter']],
  ['立即发送', ['⌘', 'Enter']],
  ['修改', ['E']],
  ['标记已解决', ['R']],
  ['新建', ['C']],
  ['切换主题', ['⇧', 'T']],
  ['快捷键说明', ['?']],
]

function Palette({ open, setOpen }: { open: boolean; setOpen: (v: boolean) => void }) {
  const nav = useNavigate()
  const { data: incidents = [] } = useQuery({ queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents'), enabled: open })
  const { data: groups = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups'), enabled: open })
  const go = (to: string) => { setOpen(false); nav(to) }
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/30 px-4 pt-[14vh] backdrop-blur-[2px]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12 }}
          onClick={() => setOpen(false)}>
          <motion.div role="dialog" aria-modal onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, scale: 0.96, y: -8 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 600, damping: 40 }}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-panel shadow-pop">
            <Command loop label="命令面板" onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}>
              <div className="flex items-center gap-2 border-b border-line px-4">
                <Search size={16} className="text-fg-3" />
                <Command.Input autoFocus placeholder="搜索页面、事件、群…" className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-3 focus-visible:outline-none" />
                <Kbd>Esc</Kbd>
              </div>
              <Command.List className="max-h-[52vh] overflow-y-auto p-1.5">
                <Command.Empty className="p-6 text-center text-sm text-fg-3">没有匹配项</Command.Empty>
                <Command.Group heading="页面">
                  {NAV.map((n) => (
                    <Item key={n.to} onSelect={() => go(n.to)} icon={n.icon} hint={<><Kbd>G</Kbd><Kbd>{n.key.toUpperCase()}</Kbd></>}>{n.label}</Item>
                  ))}
                </Command.Group>
                {incidents.length > 0 && (
                  <Command.Group heading="事件">
                    {incidents.slice(0, 30).map((i) => (
                      <Item key={i.id} value={`事件 ${i.id} ${i.summary} ${i.model} ${i.request_id}`} icon={<Zap size={14} />}
                        onSelect={() => go(`/incidents?open=${i.id}`)} hint={<span className="text-[11px] text-fg-3">#{i.id}</span>}>
                        <span className="truncate">{i.summary || '—'}</span>
                      </Item>
                    ))}
                  </Command.Group>
                )}
                {groups.length > 0 && (
                  <Command.Group heading="群">
                    {groups.slice(0, 50).map((g) => (
                      <Item key={g.id} value={`群 ${g.name} ${g.tags.join(' ')} ${g.models.join(' ')}`} icon={<MessagesSquare size={14} />}
                        onSelect={() => go(`/groups?open=${g.id}`)}>{g.name}</Item>
                    ))}
                  </Command.Group>
                )}
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function Item({ children, icon, hint, onSelect, value }: { children: ReactNode; icon: ReactNode; hint?: ReactNode; onSelect: () => void; value?: string }) {
  return (
    <Command.Item value={value} onSelect={onSelect}
      className="flex h-10 cursor-pointer items-center gap-2.5 rounded-lg px-3 text-[13px] text-fg-2 data-[selected=true]:text-fg">
      <span className="text-fg-3">{icon}</span>
      <span className="flex min-w-0 flex-1 items-center">{children}</span>
      {hint && <span className="flex gap-0.5">{hint}</span>}
    </Command.Item>
  )
}

function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-[60] grid place-items-center bg-black/30 p-4 backdrop-blur-[2px]" onClick={onClose}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <motion.div role="dialog" aria-modal onClick={(e) => e.stopPropagation()}
            initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.97, opacity: 0 }}
            className="w-full max-w-sm rounded-2xl border border-line bg-panel p-5 shadow-pop">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Keyboard size={16} />键盘快捷键</h2>
            <ul className="space-y-2 text-[13px]">
              {SHORTCUTS.map(([label, keys]) => (
                <li key={label} className="flex items-center justify-between text-fg-2">
                  {label}<span className="flex gap-1">{keys.map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
                </li>
              ))}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function Shell() {
  const qc = useQueryClient()
  const nav = useNavigate()
  const loc = useLocation()
  const { theme, cycle } = useTheme()
  const [palette, setPalette] = useState(false)
  const [help, setHelp] = useState(false)
  const { data: waiting = 0 } = useQuery({
    queryKey: ['outbound'], queryFn: () => api<Outbound[]>('/outbound'),
    select: (rows) => rows.filter((o) => ['pending', 'needs_approval', 'failed'].includes(o.status)).length,
  })
  const { data: active = 0 } = useQuery({
    queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents'),
    select: (rows) => rows.filter((i) => i.status !== 'resolved').length,
  })
  const counts: Record<string, number> = { '/presend': waiting, '/incidents': active }

  useEffect(() => {
    document.title = waiting ? `(${waiting}) PlatformLink` : 'PlatformLink'
  }, [waiting])

  useHotkeys({
    'mod+k': () => setPalette((v) => !v),
    'shift+?': () => setHelp((v) => !v),
    '?': () => setHelp((v) => !v),
    't': (e) => { if (e.shiftKey) cycle() },
    ...Object.fromEntries(NAV.map((n) => [`g ${n.key}`, () => nav(n.to)])),
  })

  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  return (
    <div className="min-h-screen md:flex">
      <aside className="sticky top-0 z-30 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-panel/60 px-3 py-4 backdrop-blur md:flex">
        <div className="mb-5 flex items-center gap-2 px-2">
          <div className="grid size-7 place-items-center rounded-lg bg-accent text-[13px] font-bold text-accent-fg shadow-card">P</div>
          <span className="text-sm font-semibold tracking-tight">PlatformLink</span>
        </div>
        <button onClick={() => setPalette(true)}
          className="mb-4 flex h-8 items-center gap-2 rounded-lg border border-line bg-panel px-2.5 text-[13px] text-fg-3 transition-colors hover:border-line-strong hover:text-fg-2">
          <Search size={14} />搜索<span className="ml-auto flex gap-0.5"><Kbd>⌘</Kbd><Kbd>K</Kbd></span>
        </button>
        <nav className="space-y-0.5">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => clsx(
              'relative flex h-8 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors',
              isActive ? 'font-medium text-fg' : 'text-fg-2 hover:bg-panel-2 hover:text-fg')}>
              {({ isActive }) => (
                <>
                  {isActive && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg bg-panel-2 ring-1 ring-line"
                    transition={{ type: 'spring', stiffness: 500, damping: 38 }} />}
                  <span className="relative text-fg-3">{n.icon}</span>
                  <span className="relative">{n.label}</span>
                  {!!counts[n.to] && (
                    <motion.span key={counts[n.to]} initial={{ scale: 0.6 }} animate={{ scale: 1 }}
                      className="relative ml-auto rounded-full bg-accent px-1.5 text-[10px] leading-4 font-semibold text-accent-fg tabular-nums">
                      {counts[n.to]}
                    </motion.span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex items-center gap-1 px-1">
          <button className="grid size-8 place-items-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg" title="切换主题 (⇧T)" onClick={cycle}><ThemeIcon size={15} /></button>
          <button className="grid size-8 place-items-center rounded-lg text-fg-3 hover:bg-panel-2 hover:text-fg" title="快捷键 (?)" onClick={() => setHelp(true)}><Keyboard size={15} /></button>
          <button className="ml-auto flex h-8 items-center gap-1.5 rounded-lg px-2 text-[13px] text-fg-3 hover:bg-panel-2 hover:text-fg"
            onClick={() => { setToken(''); qc.clear() }}><LogOut size={14} />退出</button>
        </div>
      </aside>

      {/* 移动端底部导航 */}
      <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-panel/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        {NAV.slice(0, 5).map((n) => (
          <NavLink key={n.to} to={n.to} className={({ isActive }) => clsx('relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]',
            isActive ? 'text-accent' : 'text-fg-3')}>
            {n.icon}{n.label.slice(0, 2)}
            {!!counts[n.to] && <span className="absolute top-1 left-1/2 ml-2 rounded-full bg-bad px-1 text-[9px] leading-3.5 text-white">{counts[n.to]}</span>}
          </NavLink>
        ))}
      </nav>

      <main className="min-w-0 flex-1 px-4 pt-7 pb-24 md:px-10 md:pt-12 md:pb-12">
        <div className="mx-auto max-w-4xl">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={loc.pathname} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}>
              <Suspense fallback={<Skeleton />}>
              <Routes location={loc}>
                <Route path="/" element={<Navigate to="/presend" replace />} />
                <Route path="/presend" element={<Presend />} />
                <Route path="/incidents" element={<Incidents />} />
                <Route path="/workflows" element={<Workflows />} />
                <Route path="/knowledge" element={<KnowledgePage />} />
                <Route path="/groups" element={<Groups />} />
                <Route path="/staff" element={<StaffPage />} />
                <Route path="/stats" element={<StatsPage />} />
                <Route path="*" element={<Navigate to="/presend" replace />} />
              </Routes>
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
      <Palette open={palette} setOpen={setPalette} />
      <ShortcutHelp open={help} onClose={() => setHelp(false)} />
    </div>
  )
}

export default function App() {
  const authed = useAuthed()
  return (
    <>
      {authed ? <Shell /> : <Login />}
      <Toaster position="bottom-right" theme="system" richColors closeButton
        toastOptions={{ className: '!rounded-xl !text-[13px]' }} />
    </>
  )
}
