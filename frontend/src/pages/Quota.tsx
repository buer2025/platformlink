import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Gauge, Megaphone } from 'lucide-react'
import { motion } from 'motion/react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { ago, api, post } from '../api'
import { Avatar, Badge, Button, Card, Empty, Input, listItem, PageHeader, Skeleton } from '../ui'

type Supplier = {
  group_id: number; name: string; models: string[]; channel_ids: number[]
  open_incidents: number; incident_ids: number[]
  customers: { name: string; issues: string[] }[]
  usage_24h: { rate_limited: number; errors: number; success: number }
  last_push: { at: string; status: string; text: string } | null
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] text-fg-3">{label}</div>
      <div className={clsx('tnum mt-0.5 text-[20px] leading-none font-semibold tracking-tight', tone)}>{value}</div>
    </div>
  )
}

function SupplierCard({ v }: { v: Supplier }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [note, setNote] = useState('')
  const push = useMutation({
    mutationFn: () => post(`/quota/${v.group_id}/push`, { note }),
    onSuccess: () => {
      setNote('')
      toast.success('催量消息已起草', { description: '在预发队列确认后发出', action: { label: '去确认', onClick: () => nav('/presend') } })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['quota'] }); qc.invalidateQueries({ queryKey: ['outbound'] }) },
  })
  const u = v.usage_24h
  const rate = u.rate_limited + u.success ? Math.round((u.rate_limited / (u.rate_limited + u.success)) * 100) : 0
  return (
    <motion.div layout {...listItem}>
      <Card className="overflow-hidden">
        <header className="flex items-center gap-3 px-5 pt-5">
          <Avatar name={v.name} size={36} square />
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[15px] font-semibold">{v.name}</h3>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {v.models.map((m) => <Badge key={m} tone="violet">{m}</Badge>)}
              {v.channel_ids.length === 0 && <span className="text-[11px] text-fg-3">未绑定渠道 ID，无法统计 429</span>}
            </div>
          </div>
          {v.open_incidents > 0 && <Badge tone="warn" dot>{v.open_incidents} 个待解决</Badge>}
        </header>
        <div className="grid grid-cols-4 gap-4 px-5 py-4">
          <Metric label="24h 限流 429" value={u.rate_limited} tone={u.rate_limited ? 'text-bad' : ''} />
          <Metric label="24h 错误" value={u.errors} />
          <Metric label="24h 成功" value={u.success} />
          <Metric label="限流占比 %" value={rate} tone={rate >= 10 ? 'text-bad' : rate >= 3 ? 'text-warn' : ''} />
        </div>
        {v.customers.length > 0 && (
          <div className="border-t border-line px-5 py-3">
            <div className="mb-1.5 text-[11px] text-fg-3">受影响的客户</div>
            <ul className="space-y-1 text-[13px]">
              {v.customers.map((c) => (
                <li key={c.name} className="flex gap-2"><span className="shrink-0 font-medium">{c.name}</span><span className="truncate text-fg-3">{c.issues[0]}</span></li>
              ))}
            </ul>
          </div>
        )}
        <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-panel-2/40 px-5 py-3">
          <Input className="min-w-40 flex-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="补充一句（可选）：例如客户要 300 RPM、周五前" />
          <Button variant="primary" icon={<Megaphone size={14} />} disabled={push.isPending} onClick={() => push.mutate()}>
            {push.isPending ? 'AI 起草中…' : '起草催量'}
          </Button>
          {v.last_push && (
            <span className="w-full text-[11.5px] text-fg-3">上次催量 {ago(v.last_push.at)} · {({ sent: '已发送', needs_approval: '待确认', cancelled: '已取消', pending: '倒计时', failed: '发送失败' } as Record<string, string>)[v.last_push.status] ?? v.last_push.status}</span>
          )}
        </footer>
      </Card>
    </motion.div>
  )
}

export default function QuotaPage() {
  const { data, isLoading } = useQuery({ queryKey: ['quota'], queryFn: () => api<Supplier[]>('/quota'), refetchInterval: 30000 })
  return (
    <div>
      <PageHeader title="催量中心" desc="按供应商汇总客户遇到的 429 和用量需求。点「起草催量」由 AI 带上真实数字、按这个供应商群的说话风格写好，确认后发出。同一供应商未解决的限流事件 ≥ 3 时会自动起草（2 小时最多一次）。" />
      {isLoading ? <Skeleton /> : !data?.length ? (
        <Empty icon={<Gauge size={18} />} title="还没有供应商群">在「群与标记」里把供应商的群设为供应商群，并绑定模型和渠道 ID。</Empty>
      ) : <div className="space-y-4">{data.map((v) => <SupplierCard key={v.group_id} v={v} />)}</div>}
    </div>
  )
}
