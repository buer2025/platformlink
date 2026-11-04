import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, platformLabel, post, type Platform, type Staff } from '../api'
import { btn, Empty, ErrorText, input } from '../ui'

export default function StaffPage() {
  const qc = useQueryClient()
  const { data = [], error } = useQuery({ queryKey: ['staff'], queryFn: () => api<Staff[]>('/staff') })
  const [f, setF] = useState({ name: '', platform: 'telegram' as Platform, account: '' })
  const add = useMutation({
    mutationFn: () => post('/staff', f),
    onSuccess: () => setF({ ...f, name: '', account: '' }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['staff'] }),
  })
  const del = useMutation({
    mutationFn: (id: number) => api(`/staff/${id}`, { method: 'DELETE' }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['staff'] }),
  })
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        登记我方技术支持在各平台的账号（Telegram 用户名 / 飞书 open_id / 企业微信 userid）。他们在群里的发言不会被当成客户问题。
      </p>
      <form className="grid gap-2 rounded-lg border bg-white p-4 sm:grid-cols-4"
        onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
        <input className={input} placeholder="姓名" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <select className={input} value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value as Platform })}>
          {Object.entries(platformLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input className={input} placeholder="平台账号" value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })} />
        <button className={btn.primary} disabled={!f.name.trim() || !f.account.trim() || add.isPending}>添加</button>
        <div className="sm:col-span-4"><ErrorText error={add.error} /></div>
      </form>
      <ErrorText error={error} />
      {data.length === 0 && <Empty>还没有登记我方人员</Empty>}
      <ul className="divide-y rounded-lg border bg-white">
        {data.map((s) => (
          <li key={s.id} className="flex items-center gap-3 p-3 text-sm">
            <span className="font-medium">{s.name}</span>
            <span className="text-slate-500">{platformLabel[s.platform]}</span>
            <span className="font-mono">{s.account}</span>
            <button className="ml-auto text-red-600" onClick={() => confirm(`删除 ${s.name}？`) && del.mutate(s.id)}>删除</button>
          </li>
        ))}
      </ul>
    </div>
  )
}
