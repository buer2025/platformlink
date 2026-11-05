import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { AlarmClock, Bot, Clock, Pencil, Siren, Zap } from 'lucide-react'
import { motion } from 'motion/react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, categoryLabel, respLabel } from '../api'
import { Card, Empty, PageHeader, Segmented, Skeleton, Stat } from '../ui'

type Stats = {
  days: number; incidents: number; resolved: number; escalated: number
  first_response_median_min: number | null; resolution_median_min: number | null
  replies_sent: number; ai_adopted: number; ai_auto: number; human_edited: number; manual_messages: number
  by_category: [string, number][]; by_responsible: [string, number][]
  series: { day: string; count: number }[]
  waiting: { incident_id: number; group: string; summary: string; last_message: string; waiting_min: number }[]
}

const fmtMin = (m: number | null) => m == null ? '—' : m < 1 ? `${Math.round(m * 60)} 秒` : m < 60 ? `${m.toFixed(m < 10 ? 1 : 0)} 分钟` : `${(m / 60).toFixed(1)} 小时`
const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0)

/** 单序列柱状图：每天新事件数。悬停显示数值。 */
function DailyBars({ series }: { series: Stats['series'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...series.map((d) => d.count))
  return (
    <div>
      <div className="relative flex h-36 items-end gap-[2px]" onMouseLeave={() => setHover(null)}>
        {series.map((d, i) => (
          <div key={d.day} className="relative flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}>
            <motion.div initial={{ height: 0 }} animate={{ height: `${(d.count / max) * 100}%` }}
              transition={{ type: 'spring', stiffness: 260, damping: 30, delay: i * 0.02 }}
              className={clsx('w-full rounded-t-[4px] transition-opacity', d.count ? 'min-h-[2px] bg-accent' : '', hover !== null && hover !== i && 'opacity-50')} />
            {hover === i && (
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 -translate-x-1/2 rounded-lg bg-fg px-2 py-1 text-[11px] whitespace-nowrap text-bg shadow-pop">
                {d.day} · {d.count} 个事件
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex justify-between border-t border-line pt-1.5 text-[10.5px] text-fg-3 tnum">
        <span>{series[0]?.day}</span><span>{series[series.length - 1]?.day}</span>
      </div>
    </div>
  )
}

/** 单色横条：各类别 / 责任方占比，直接标注数值。 */
function Breakdown({ rows, label }: { rows: [string, number][]; label: (k: string) => string }) {
  const total = rows.reduce((a, [, n]) => a + n, 0) || 1
  return (
    <ul className="space-y-2.5">
      {rows.map(([k, n]) => (
        <li key={k}>
          <div className="mb-1 flex justify-between text-[12.5px]"><span className="text-fg-2">{label(k)}</span><span className="tnum text-fg">{n}</span></div>
          <div className="h-1.5 rounded-full bg-panel-2">
            <motion.div className="h-full rounded-full bg-accent" initial={{ width: 0 }} animate={{ width: `${(n / total) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function StatsPage() {
  const [days, setDays] = useState<'7' | '30'>('7')
  const { data, isLoading } = useQuery({ queryKey: ['stats', days], queryFn: () => api<Stats>(`/stats?days=${days}`), refetchInterval: 15000 })
  return (
    <div>
      <PageHeader title="数据看板" desc="Agent 帮了多少忙、客户等了多久、哪里还需要人。">
        <Segmented value={days} onChange={setDays} options={[{ value: '7', label: '近 7 天' }, { value: '30', label: '近 30 天' }]} />
      </PageHeader>
      {isLoading || !data ? <Skeleton /> : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="首次响应（中位数）" value={fmtMin(data.first_response_median_min)} hint="客户发消息到我们第一次回复" icon={<Clock size={13} />} tone="info" />
            <Stat label="AI 原稿采用率" value={`${pct(data.ai_adopted, data.replies_sent)}%`} hint={`${data.ai_adopted}/${data.replies_sent} 条回复没改直接发`} icon={<Bot size={13} />} tone="ok" />
            <Stat label="人工修改" value={data.human_edited} hint={`改法已用于学习 · 另有 ${data.manual_messages} 条人工撰写`} icon={<Pencil size={13} />} tone="violet" />
            <Stat label="升级人工" value={data.escalated} hint={`占事件 ${pct(data.escalated, data.incidents)}%`} icon={<Siren size={13} />} tone="warn" />
          </div>

          {data.waiting.length > 0 && (
            <Card className="overflow-hidden">
              <header className="flex items-center gap-2 border-b border-line px-5 py-3 text-[13px] font-semibold">
                <AlarmClock size={15} className="text-bad" />正在等我们回复的客户 <span className="tnum font-normal text-fg-3">{data.waiting.length}</span>
              </header>
              <ul className="divide-y divide-line">
                {data.waiting.map((w) => (
                  <li key={w.incident_id}>
                    <Link to={`/incidents?open=${w.incident_id}`} className="flex items-center gap-4 px-5 py-3 transition-colors hover:bg-panel-2">
                      <span className={clsx('tnum w-16 shrink-0 text-[13px] font-semibold', w.waiting_min >= 30 ? 'text-bad' : w.waiting_min >= 10 ? 'text-warn' : 'text-fg-2')}>
                        {fmtMin(w.waiting_min)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px]">「{w.last_message}」</span>
                        <span className="block truncate text-xs text-fg-3">{w.group} · {w.summary}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid gap-4 md:grid-cols-[1.6fr_1fr]">
            <Card className="p-5">
              <div className="mb-4 flex items-baseline justify-between">
                <h3 className="text-[13px] font-semibold">每天新事件</h3>
                <span className="text-xs text-fg-3 tnum">共 {data.incidents} 个 · 已解决 {data.resolved} · 解决用时中位数 {fmtMin(data.resolution_median_min)}</span>
              </div>
              {data.incidents ? <DailyBars series={data.series} /> : <Empty icon={<Zap size={18} />} title="这段时间没有事件" />}
            </Card>
            <Card className="space-y-6 p-5">
              <div>
                <h3 className="mb-3 text-[13px] font-semibold">问题类型</h3>
                <Breakdown rows={data.by_category} label={(k) => categoryLabel[k] ?? k} />
              </div>
              <div>
                <h3 className="mb-3 text-[13px] font-semibold">责任方</h3>
                <Breakdown rows={data.by_responsible} label={(k) => respLabel[k]?.[0] ?? (k === 'pending' ? '排查中' : k)} />
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
