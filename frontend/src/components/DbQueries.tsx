import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { ChevronRight, CircleAlert, Database } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { api, clock, dbLabel, fmtMs, fmtTime, type DbQuery } from '../api'
import { Badge } from '../ui'

/** 耗时着色：≥1s 红、≥100ms 黄，其余正常。 */
const msTone = (ms: number) => (ms >= 1000 ? 'text-bad' : ms >= 100 ? 'text-warn' : 'text-fg-3')

export function QueryRow({ q, extra }: { q: DbQuery; extra?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-line last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-1 py-2.5 text-left text-[13px] hover:bg-panel-2/50">
        <ChevronRight size={14} className={clsx('shrink-0 text-fg-3 transition-transform duration-200', open && 'rotate-90')} />
        <Badge tone={q.db === 'newapi' ? 'violet' : 'slate'}>{q.db === 'newapi' ? 'new-api' : '业务库'}</Badge>
        <span className="min-w-0 truncate font-mono text-xs">{q.caller || q.op.toUpperCase()}</span>
        {extra}
        <span className="ml-auto flex shrink-0 items-center gap-3 text-xs tabular-nums">
          {q.error ? <span className="inline-flex items-center gap-1 text-bad"><CircleAlert size={12} />出错</span>
            : <span className={msTone(q.duration_ms)}>{fmtMs(q.duration_ms)}</span>}
          <span className="hidden w-14 text-right text-fg-3 sm:inline">{q.rows >= 0 ? `${q.rows} 行` : ''}</span>
          <span className="w-16 text-right text-fg-3" title={fmtTime(q.created_at)}>{clock(q.created_at, true)}</span>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden px-1 pb-2">
            <div className="mb-1 text-[11px] text-fg-3">{dbLabel[q.db] ?? q.db} · {fmtTime(q.created_at)}{q.incident_id ? ` · 事件 #${q.incident_id}` : ''}</div>
            {q.error && <div className="mb-2 rounded-lg bg-bad-soft px-2.5 py-1.5 text-xs text-bad">{q.error}</div>}
            <pre className="max-h-60 overflow-auto rounded-lg border border-line bg-panel p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-fg-2">{q.statement}</pre>
            {q.params && q.params !== '()' && (
              <pre className="mt-1.5 max-h-32 overflow-auto rounded-lg border border-line bg-panel p-2.5 font-mono text-[11px] whitespace-pre-wrap text-fg-3">参数 {q.params}</pre>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

/** 排查某个事件时查了哪些日志、各花多久。 */
export function IncidentQueries({ incidentId }: { incidentId: number }) {
  const { data = [] } = useQuery({
    queryKey: ['incident-queries', incidentId],
    queryFn: () => api<DbQuery[]>(`/incidents/${incidentId}/db-queries`),
  })
  if (!data.length) return (
    <p className="flex items-center justify-center gap-1.5 py-6 text-[13px] text-fg-3"><Database size={14} />还没有日志库查询</p>
  )
  const ms = data.reduce((a, q) => a + q.duration_ms, 0)
  return (
    <div>
      <div className="mb-1 flex gap-4 text-xs text-fg-3 tabular-nums">
        <span>{data.length} 次查询</span><span>累计 {fmtMs(ms)}</span>
        <span>最慢 {fmtMs(Math.max(...data.map((q) => q.duration_ms)))}</span>
      </div>
      <ul>{data.map((q) => <QueryRow key={q.id} q={q} />)}</ul>
    </div>
  )
}
