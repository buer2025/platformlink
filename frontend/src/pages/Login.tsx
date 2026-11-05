import { motion } from 'motion/react'
import { useState } from 'react'
import { setToken } from '../api'
import { Button, Input } from '../ui'

export default function Login() {
  const [token, setValue] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      // 先验证口令，通过后再保存，避免界面在验证期间来回切换
      const r = await fetch('/api/me', { headers: { Authorization: `Bearer ${token.trim()}` } })
      if (r.ok) {
        setToken(token.trim())
        return
      }
      setErr(r.status === 401 ? '口令错误' : `服务器错误 ${r.status}`)
    } catch {
      setErr('无法连接服务器')
    }
    setBusy(false)
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[420px] w-[720px] -translate-x-1/2 rounded-full bg-accent/15 blur-3xl" />
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className="relative w-full max-w-sm rounded-2xl border border-line bg-panel p-7 shadow-pop">
        <div className="mb-6 flex items-center gap-2.5">
          <div className="grid size-9 place-items-center rounded-xl bg-accent text-sm font-bold text-accent-fg">P</div>
          <div>
            <h1 className="text-[15px] font-semibold">PlatformLink 登录</h1>
            <p className="text-xs text-fg-3">客户 · 供应商群智能协同</p>
          </div>
        </div>
        <label className="block">
          <span className="text-xs font-medium text-fg-2">管理口令</span>
          <Input className="mt-1.5 h-9" type="password" value={token} autoFocus
            onChange={(e) => setValue(e.target.value)} placeholder="服务端 ADMIN_TOKEN" />
        </label>
        <motion.p animate={err ? { x: [0, -6, 6, -4, 4, 0] } : {}} transition={{ duration: 0.35 }}
          className="mt-2 h-4 text-xs text-bad">{err}</motion.p>
        <Button variant="primary" className="mt-3 h-9 w-full" disabled={!token.trim() || busy}>
          {busy ? '验证中…' : '登录'}
        </Button>
      </motion.form>
    </div>
  )
}
