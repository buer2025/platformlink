import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Outbound } from '../api'

const purposeLabel: Record<string, string> = {
  ack: '安抚回复',
  customer_reply: '客户回复',
  supplier_ticket: '供应商转交',
  quota_push: '催量',
}

const statusLabel: Record<Outbound['status'], string> = {
  pending: '倒计时中',
  needs_approval: '需人工确认',
  sent: '已发送',
  cancelled: '已取消',
  failed: '发送失败',
}

function useNow() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

function scoreColor(s: number) {
  return s >= 90 ? 'bg-green-100 text-green-700' : s >= 70 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'
}

function Item({ o, now }: { o: Outbound; now: number }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(o.text)
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: unknown }) =>
      api(`/outbound/${o.id}/${action}`, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
    onSuccess: () => {
      setEditing(false)
      qc.invalidateQueries({ queryKey: ['outbound'] })
    },
  })
  const open = o.status === 'pending' || o.status === 'needs_approval'
  const left = o.send_at ? Math.max(0, Math.round((Date.parse(o.send_at) - now) / 1000)) : null

  return (
    <div className="rounded-lg border bg-white p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{o.group_name}</span>
        <span className="rounded bg-slate-100 px-2 py-0.5">{purposeLabel[o.purpose] ?? o.purpose}</span>
        <span className={`rounded px-2 py-0.5 ${scoreColor(o.score)}`}>准确率 {o.score}</span>
        <span className="text-slate-500">{statusLabel[o.status]}</span>
        {o.status === 'pending' && left !== null && (
          <span className="ml-auto font-mono text-blue-600">{left}s 后自动发送</span>
        )}
      </div>
      {o.mentions.length > 0 && <div className="mb-1 text-sm text-slate-500">@{o.mentions.join(' @')}</div>}
      {editing ? (
        <textarea className="w-full rounded border p-2" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      ) : (
        <p className="whitespace-pre-wrap">{o.text}</p>
      )}
      {o.error && <p className="mt-1 text-sm text-red-600">{o.error}</p>}
      {open && (
        <div className="mt-3 flex gap-2 text-sm">
          {editing ? (
            <>
              <button className="rounded bg-blue-600 px-3 py-1 text-white" onClick={() => act.mutate({ action: 'edit', body: { text } })}>保存</button>
              <button className="rounded border px-3 py-1" onClick={() => setEditing(false)}>取消编辑</button>
            </>
          ) : (
            <>
              <button className="rounded bg-blue-600 px-3 py-1 text-white" onClick={() => act.mutate({ action: 'send' })}>立即发送</button>
              <button className="rounded border px-3 py-1" onClick={() => setEditing(true)}>修改</button>
              <button className="rounded border px-3 py-1 text-red-600" onClick={() => act.mutate({ action: 'cancel' })}>取消</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function Presend() {
  const now = useNow()
  const { data = [] } = useQuery({ queryKey: ['outbound'], queryFn: () => api<Outbound[]>('/outbound') })
  const open = data.filter((o) => o.status === 'pending' || o.status === 'needs_approval')
  const done = data.filter((o) => !open.includes(o))
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-3 text-lg font-semibold">待发送（{open.length}）</h2>
        <div className="space-y-3">
          {open.map((o) => <Item key={o.id} o={o} now={now} />)}
          {open.length === 0 && <p className="text-slate-500">暂无待发送消息</p>}
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-lg font-semibold">历史</h2>
        <div className="space-y-3">{done.map((o) => <Item key={o.id} o={o} now={now} />)}</div>
      </section>
    </div>
  )
}
