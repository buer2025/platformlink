import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, fmtTime, patch, platformLabel, type Group, type Message } from '../api'
import { Badge, btn, Empty, ErrorText, input, Loading } from '../ui'

const kindLabel = { customer: '客户群', supplier: '供应商群', internal: '内部群' }
const autoLabel = { auto: '按准确率自动', confirm: '需人工确认', observe: '只观察' }
const toList = (s: string) => s.split(/[,，\n]+/).map((x) => x.trim()).filter(Boolean)
const lines = (v: unknown) => (Array.isArray(v) ? v.join('\n') : '')
const str = (v: unknown) => (typeof v === 'string' ? v : '')

function Field({ label, hint, children, wide }: { label: string; hint?: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <label className={`block text-sm ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="text-slate-700">{label}</span>
      {hint && <span className="ml-1 text-xs text-slate-400">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  )
}

function Editor({ g, onDone }: { g: Group; onDone: () => void }) {
  const qc = useQueryClient()
  const st = g.style ?? {}
  const [f, setF] = useState({
    name: g.name, kind: g.kind, automation: g.automation, level: g.level,
    models: g.models.join(', '), contacts: g.contacts.join(', '),
    owner: g.owner, backup_owner: g.backup_owner, extra_delay_seconds: String(g.extra_delay_seconds),
    address: str(st.address), tone: str(st.tone),
    phrases: lines(st.phrases), taboos: lines(st.taboos), samples: lines(st.samples),
  })
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setF({ ...f, [k]: e.target.value })
  const save = useMutation({
    mutationFn: () => {
      const style: Record<string, unknown> = { ...st }
      const put = (k: string, v: unknown) => { if ((Array.isArray(v) && v.length) || (typeof v === 'string' && v)) style[k] = v; else delete style[k] }
      put('address', f.address.trim()); put('tone', f.tone.trim())
      put('phrases', toList(f.phrases)); put('taboos', toList(f.taboos))
      put('samples', f.samples.split('\n').map((x) => x.trim()).filter(Boolean))
      return patch(`/groups/${g.id}`, {
        name: f.name.trim() || g.name, kind: f.kind, automation: f.automation, level: f.level.trim(),
        models: toList(f.models), contacts: toList(f.contacts),
        owner: f.owner.trim(), backup_owner: f.backup_owner.trim(),
        extra_delay_seconds: Math.max(0, parseInt(f.extra_delay_seconds) || 0), style,
      })
    },
    onSuccess: onDone,
    onSettled: () => qc.invalidateQueries({ queryKey: ['groups'] }),
  })
  const { data: msgs = [] } = useQuery({ queryKey: ['group-msgs', g.id], queryFn: () => api<Message[]>(`/groups/${g.id}/messages`) })

  return (
    <div className="space-y-4 border-t bg-slate-50 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="群名称"><input className={input} value={f.name} onChange={set('name')} /></Field>
        <Field label="类型">
          <select className={input} value={f.kind} onChange={set('kind')}>
            {Object.entries(kindLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="自动化" hint="只观察：Agent 不发言">
          <select className={input} value={f.automation} onChange={set('automation')}>
            {Object.entries(autoLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        <Field label="额外倒计时（秒）" hint="VIP 群可加长">
          <input className={input} inputMode="numeric" value={f.extra_delay_seconds} onChange={set('extra_delay_seconds')} />
        </Field>
        <Field label="涉及模型" hint="逗号分隔；供应商群按此路由"><input className={input} value={f.models} onChange={set('models')} placeholder="seedance, ..." /></Field>
        <Field label="对接人" hint="转交时 @ 的账号"><input className={input} value={f.contacts} onChange={set('contacts')} /></Field>
        <Field label="等级"><input className={input} value={f.level} onChange={set('level')} placeholder="VIP / KA / 主力供应商" /></Field>
        <Field label="负责人 / 备份"><div className="flex gap-2">
          <input className={input} value={f.owner} onChange={set('owner')} placeholder="负责人" />
          <input className={input} value={f.backup_owner} onChange={set('backup_owner')} placeholder="备份" />
        </div></Field>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-medium">风格标记 <span className="text-xs font-normal text-slate-400">Agent 回复时据此调整语气</span></h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="称呼"><input className={input} value={f.address} onChange={set('address')} placeholder="王总 / 哥 / 老师" /></Field>
          <Field label="语气"><input className={input} value={f.tone} onChange={set('tone')} placeholder="正式 / 轻松 / 熟络 / 英文" /></Field>
          <Field label="口头禅" hint="逗号或换行分隔"><textarea className={input} rows={2} value={f.phrases} onChange={set('phrases')} placeholder="辛苦啦，麻烦看下" /></Field>
          <Field label="禁忌" hint="逗号或换行分隔"><textarea className={input} rows={2} value={f.taboos} onChange={set('taboos')} placeholder="不提竞品" /></Field>
          <Field label="风格样本" hint="每行一条我方在该群的典型发言" wide>
            <textarea className={input} rows={3} value={f.samples} onChange={set('samples')} />
          </Field>
        </div>
      </div>
      <ErrorText error={save.error} />
      <div className="flex gap-2">
        <button className={btn.primary} disabled={save.isPending} onClick={() => save.mutate()}>保存</button>
        <button className={btn.plain} onClick={onDone}>取消</button>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-medium">最近消息</h3>
        {msgs.length === 0 ? <p className="text-sm text-slate-500">暂无</p> : (
          <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
            {msgs.slice(0, 50).map((m) => (
              <li key={m.id} className={m.from_us ? 'text-blue-700' : ''}>
                <span className="text-xs text-slate-400">{fmtTime(m.created_at)} · {m.sender}</span> {m.text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function Groups() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const [editing, setEditing] = useState<number | null>(null)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [platform, setPlatform] = useState('')
  const rows = data.filter((g) =>
    (!kind || g.kind === kind) && (!platform || g.platform === platform) &&
    (!q || [g.name, g.owner, ...g.models].some((x) => x.toLowerCase().includes(q.toLowerCase()))))
  if (isLoading) return <Loading />
  return (
    <div className="space-y-3">
      <ErrorText error={error} />
      <div className="flex flex-wrap gap-2">
        <input className={`${input} sm:max-w-xs`} placeholder="搜索群名 / 模型 / 负责人" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className={`${input} sm:w-40`} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">全部类型</option>
          {Object.entries(kindLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className={`${input} sm:w-40`} value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">全部平台</option>
          {Object.entries(platformLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="self-center text-sm text-slate-500">{rows.length} / {data.length} 个群</span>
      </div>
      {data.length === 0 && <Empty>还没有群。机器人加入群并收到消息后会自动出现在这里（默认只观察，需打标记后才会回复）。</Empty>}
      {rows.map((g) => (
        <div key={g.id} className="overflow-hidden rounded-lg border bg-white">
          <button className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-sm" onClick={() => setEditing(editing === g.id ? null : g.id)}>
            <span className="font-medium">{g.name}</span>
            <Badge>{platformLabel[g.platform]}</Badge>
            <Badge tone="blue">{kindLabel[g.kind]}</Badge>
            <Badge tone={g.automation === 'observe' ? 'slate' : g.automation === 'auto' ? 'green' : 'amber'}>{autoLabel[g.automation]}</Badge>
            {g.level && <Badge tone="red">{g.level}</Badge>}
            {g.models.map((m) => <Badge key={m} tone="purple">{m}</Badge>)}
            {g.owner && <span className="text-slate-500">负责人 {g.owner}</span>}
            <span className="ml-auto text-blue-600">{editing === g.id ? '收起' : '编辑标记'}</span>
          </button>
          {editing === g.id && <Editor g={g} onDone={() => setEditing(null)} />}
        </div>
      ))}
    </div>
  )
}
