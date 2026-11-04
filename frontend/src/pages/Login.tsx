import { useState } from 'react'
import { setToken } from '../api'
import { btn, input } from '../ui'

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
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border bg-white p-6 shadow-sm">
        <h1 className="text-lg font-semibold">PlatformLink 登录</h1>
        <label className="block text-sm">
          管理口令
          <input className={`${input} mt-1`} type="password" value={token} autoFocus
            onChange={(e) => setValue(e.target.value)} placeholder="服务端 ADMIN_TOKEN" />
        </label>
        {err && <p className="text-sm text-red-600">{err}</p>}
        <button className={`${btn.primary} w-full`} disabled={!token.trim() || busy}>
          {busy ? '验证中…' : '登录'}
        </button>
      </form>
    </div>
  )
}
