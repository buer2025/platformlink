import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, fmtTime, post, type Outbound } from '../api'
import { Badge, btn, Empty, ErrorText, input, Loading } from '../ui'

const purposeLabel: Record<string, string> = {
  ack: '安抚回复',
  customer_reply: '客户回复',
  supplier_ticket: '供应商转交',
  supplier_remind: '供应商催办',
  quota_push: '催量',
}

const statusInfo: Record<Outbound['status'], [string, string]> = {
  pending: ['倒计时中', 'blue'],
  needs_approval: ['需人工确认', 'amber'],
  sending: ['发送中', 'blue'],
  sent: ['已发送', 'green'],
  cancelled: ['已取消', 'slate'],
  failed: ['发送失败', 'red'],
}

function useNow() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

const scoreTone = (s: number) => (s >= 90 ? 'green' : s >= 70 ? 'amber' : 'red')

function Item({ o, now }: { o: Outbound; now: number }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(o.text)
  const act = useMutation({
    mutationFn: ({ action, body }: { action: string; body?: unknown }) => post(`/outbound/${o.id}/${action}`, body),
    onSuccess: () => setEditing(false),
    onSettled: () => qc.invalidateQueries({ queryKey: ['outbound'] }),
  })
  const actionable = o.status === 'pending' || o.status === 'needs_approval' || o.status === 'failed'
  const left = o.send_at ? Math.max(0, Math.round((Date.parse(o.send_at) - now) / 1000)) : null
  const [label, tone] = statusInfo[o.status]

  return (
    <div className="rounded-lg border bg-white p-4" data-testid="outbound">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{o.group_name}</span>
        <Badge>{purposeLabel[o.purpose] ?? o.purpose}</Badge>
        <Badge tone={scoreTone(o.score)}>准确率 {o.score}</Badge>
        <Badge tone={tone}>{label}</Badge>
        {o.incident_id && <span className="text-xs text-slate-400">事件 #{o.incident_id}</span>}
        <span className="ml-auto text-xs text-slate-500">
          {o.status === 'pending' && left !== null
            ? <span className="font-mono text-sm text-blue-600">{left > 0 ? `${left}s 后自动发送` : '即将发送'}</span>
            : fmtTime(o.created_at)}
        </span>
      </div>
      {o.mentions.length > 0 && <div className="mb-1 text-sm text-slate-500">@{o.mentions.join(' @')}</div>}
      {editing ? (
        <textarea className={input} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      ) : (
        <p className="text-sm whitespace-pre-wrap">{o.text}</p>
      )}
      {o.error && <p className="mt-1 text-sm text-red-600">{o.error}</p>}
      <ErrorText error={act.error} />
      {actionable && (
        <div className="mt-3 flex flex-wrap gap-2">
          {editing ? (
            <>
              <button className={btn.primary} disabled={act.isPending || !text.trim()}
                onClick={() => act.mutate({ action: 'edit', body: { text } })}>保存（之后需确认发送）</button>
              <button className={btn.plain} onClick={() => { setEditing(false); setText(o.text) }}>取消编辑</button>
            </>
          ) : (
            <>
              <button className={btn.primary} disabled={act.isPending} onClick={() => act.mutate({ action: 'send' })}>
                {o.status === 'failed' ? '重新发送' : '立即发送'}
              </button>
              <button className={btn.plain} onClick={() => { setText(o.text); setEditing(true) }}>修改</button>
              <button className={btn.danger} disabled={act.isPending}
                onClick={() => confirm('确定取消这条消息？') && act.mutate({ action: 'cancel' })}>取消</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function Presend() {
  const now = useNow()
  const { data = [], isLoading, error } = useQuery({ queryKey: ['outbound'], queryFn: () => api<Outbound[]>('/outbound') })
  const isOpen = (o: Outbound) => ['pending', 'needs_approval', 'failed', 'sending'].includes(o.status)
  const open = data.filter(isOpen)
  const done = data.filter((o) => !isOpen(o))
  if (isLoading) return <Loading />
  return (
    <div className="space-y-6">
      <ErrorText error={error} />
      <section>
        <h2 className="mb-3 text-lg font-semibold">待处理（{open.length}）</h2>
        <div className="space-y-3">
          {open.map((o) => <Item key={o.id} o={o} now={now} />)}
          {open.length === 0 && <Empty>暂无待发送消息</Empty>}
        </div>
      </section>
      {done.length > 0 && (
        <section>
          <h2 className="mb-3 text-lg font-semibold">历史</h2>
          <div className="space-y-3">{done.map((o) => <Item key={o.id} o={o} now={now} />)}</div>
        </section>
      )}
    </div>
  )
}
