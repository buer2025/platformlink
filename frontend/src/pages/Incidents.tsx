import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, fmtTime, patch, type Group, type Incident, type Message } from '../api'
import { Badge, btn, Empty, ErrorText, Loading } from '../ui'

const respLabel: Record<string, [string, string]> = {
  customer: ['客户侧', 'blue'], us: ['我方', 'purple'], supplier: ['供应商', 'amber'], unknown: ['待定', 'slate'],
}
const statusLabel: Record<Incident['status'], [string, string]> = {
  open: ['新建', 'slate'], investigating: ['排查中', 'blue'], waiting_supplier: ['等待供应商', 'amber'], resolved: ['已解决', 'green'],
}
const catLabel: Record<string, string> = { error: '调用报错', rate_limit: '429 限流', other: '其他' }

function Detail({ i, groups }: { i: Incident; groups: Map<number, Group> }) {
  const qc = useQueryClient()
  const { data: msgs = [] } = useQuery({
    queryKey: ['incident-msgs', i.id],
    queryFn: () => api<Message[]>(`/incidents/${i.id}/messages`),
  })
  const update = useMutation({
    mutationFn: (status: Incident['status']) => patch(`/incidents/${i.id}`, { status }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['incidents'] }),
  })
  return (
    <div className="space-y-3 border-t bg-slate-50 p-4 text-sm">
      <div className="grid gap-2 sm:grid-cols-2">
        <div><span className="text-slate-500">request_id：</span><span className="font-mono">{i.request_id || '—'}</span></div>
        <div><span className="text-slate-500">模型：</span>{i.model || '—'}</div>
        <div><span className="text-slate-500">供应商群：</span>{i.supplier_group_id ? groups.get(i.supplier_group_id)?.name : '—'}</div>
        <div><span className="text-slate-500">转交时间：</span>{fmtTime(i.ticket_sent_at)}</div>
        <div><span className="text-slate-500">供应商回复：</span>{fmtTime(i.supplier_replied_at)}</div>
        <div><span className="text-slate-500">已催办：</span>{i.reminders} 次</div>
      </div>
      <div><span className="text-slate-500">排查依据：</span><span className="whitespace-pre-wrap">{i.evidence || '—'}</span></div>
      {i.supplier_last_reply && <div><span className="text-slate-500">供应商最新回复：</span>{i.supplier_last_reply}</div>}
      <div>
        <div className="mb-1 text-slate-500">相关消息</div>
        <ul className="space-y-1">
          {msgs.map((m) => (
            <li key={m.id} className={m.from_us ? 'text-blue-700' : ''}>
              <span className="text-xs text-slate-400">{fmtTime(m.created_at)} · {groups.get(m.group_id)?.name} · {m.sender}</span>
              <div className="whitespace-pre-wrap">{m.text}</div>
            </li>
          ))}
        </ul>
      </div>
      <ErrorText error={update.error} />
      <div className="flex gap-2">
        {i.status !== 'resolved'
          ? <button className={btn.primary} onClick={() => update.mutate('resolved')}>标记已解决</button>
          : <button className={btn.plain} onClick={() => update.mutate('investigating')}>重新打开</button>}
      </div>
    </div>
  )
}

export default function Incidents() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents') })
  const { data: groupList = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const groups = new Map(groupList.map((g) => [g.id, g]))
  const [open, setOpen] = useState<number | null>(null)
  const [onlyOpen, setOnlyOpen] = useState(true)
  const rows = onlyOpen ? data.filter((i) => i.status !== 'resolved') : data
  if (isLoading) return <Loading />
  return (
    <div className="space-y-3">
      <ErrorText error={error} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> 只看未解决
      </label>
      {rows.length === 0 && <Empty>暂无事件</Empty>}
      {rows.map((i) => {
        const [rl, rt] = respLabel[i.responsible] ?? [i.responsible || '—', 'slate']
        const [sl, st] = statusLabel[i.status]
        const overdue = i.status === 'waiting_supplier' && i.ticket_sent_at && !i.supplier_replied_at && i.reminders > 0
        return (
          <div key={i.id} className="overflow-hidden rounded-lg border bg-white">
            <button className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-sm" onClick={() => setOpen(open === i.id ? null : i.id)}>
              <span className="text-slate-400">#{i.id}</span>
              <span className="font-medium">{groups.get(i.group_id)?.name}</span>
              <Badge>{catLabel[i.category] ?? i.category}</Badge>
              <Badge tone={rt}>{rl}</Badge>
              <Badge tone={st}>{sl}</Badge>
              {overdue && <Badge tone="red">供应商未回复</Badge>}
              {i.related_messages > 1 && <span className="text-xs text-slate-500">合并 {i.related_messages} 条</span>}
              <span className="text-xs text-slate-500">置信度 {i.confidence}</span>
              <span className="w-full truncate text-slate-600 sm:ml-auto sm:w-auto sm:max-w-md">{i.summary}</span>
            </button>
            {open === i.id && <Detail i={i} groups={groups} />}
          </div>
        )
      })}
    </div>
  )
}
