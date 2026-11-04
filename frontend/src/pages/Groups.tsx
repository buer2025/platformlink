import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Group } from '../api'

const kindLabel = { customer: '客户群', supplier: '供应商群', internal: '内部群' }
const autoLabel = { auto: '按准确率自动', confirm: '需人工确认', observe: '只观察' }
const list = (s: string) => s.split(/[,，\s]+/).filter(Boolean)

function Editor({ g, onDone }: { g: Group; onDone: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState({
    kind: g.kind,
    automation: g.automation,
    models: g.models.join(', '),
    contacts: g.contacts.join(', '),
    level: g.level,
    owner: g.owner,
    style: JSON.stringify(g.style, null, 2),
  })
  const [err, setErr] = useState('')
  const save = useMutation({
    mutationFn: () =>
      api(`/groups/${g.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          kind: f.kind,
          automation: f.automation,
          models: list(f.models),
          contacts: list(f.contacts),
          level: f.level,
          owner: f.owner,
          style: JSON.parse(f.style || '{}'),
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['groups'] })
      onDone()
    },
    onError: (e) => setErr(String(e)),
  })
  const input = 'w-full rounded border px-2 py-1'
  return (
    <div className="grid gap-3 rounded-lg border bg-white p-4 sm:grid-cols-2">
      <label>类型<select className={input} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as Group['kind'] })}>
        {Object.entries(kindLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select></label>
      <label>自动化<select className={input} value={f.automation} onChange={(e) => setF({ ...f, automation: e.target.value as Group['automation'] })}>
        {Object.entries(autoLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select></label>
      <label>涉及模型（逗号分隔）<input className={input} value={f.models} onChange={(e) => setF({ ...f, models: e.target.value })} /></label>
      <label>对接人（@ 的账号）<input className={input} value={f.contacts} onChange={(e) => setF({ ...f, contacts: e.target.value })} /></label>
      <label>等级<input className={input} value={f.level} onChange={(e) => setF({ ...f, level: e.target.value })} /></label>
      <label>负责人<input className={input} value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })} /></label>
      <label className="sm:col-span-2">风格标记（JSON：称呼、语气、口头禅、禁忌、样本）
        <textarea className={`${input} font-mono`} rows={5} value={f.style} onChange={(e) => setF({ ...f, style: e.target.value })}
          placeholder='{"address": "王总", "tone": "熟络", "phrases": ["辛苦啦"]}' />
      </label>
      {err && <p className="text-red-600 sm:col-span-2">{err}</p>}
      <div className="flex gap-2 sm:col-span-2">
        <button className="rounded bg-blue-600 px-3 py-1 text-white" onClick={() => { setErr(''); try { JSON.parse(f.style || '{}'); save.mutate() } catch { setErr('风格标记不是合法 JSON') } }}>保存</button>
        <button className="rounded border px-3 py-1" onClick={onDone}>取消</button>
      </div>
    </div>
  )
}

export default function Groups() {
  const { data = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const [editing, setEditing] = useState<number | null>(null)
  return (
    <div className="space-y-3">
      {data.length === 0 && <p className="text-slate-500">还没有群。机器人加入群并收到消息后会自动出现在这里（默认只观察）。</p>}
      {data.map((g) => (
        <div key={g.id}>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-white p-3 text-sm">
            <span className="font-medium">{g.name}</span>
            <span className="rounded bg-slate-100 px-2 py-0.5">{g.platform}</span>
            <span className="rounded bg-blue-50 px-2 py-0.5 text-blue-700">{kindLabel[g.kind]}</span>
            <span className="rounded bg-slate-100 px-2 py-0.5">{autoLabel[g.automation]}</span>
            {g.models.map((m) => <span key={m} className="rounded bg-purple-50 px-2 py-0.5 text-purple-700">{m}</span>)}
            {g.owner && <span className="text-slate-500">负责人 {g.owner}</span>}
            <button className="ml-auto text-blue-600" onClick={() => setEditing(editing === g.id ? null : g.id)}>编辑标记</button>
          </div>
          {editing === g.id && <Editor g={g} onDone={() => setEditing(null)} />}
        </div>
      ))}
    </div>
  )
}
