import { useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Check, Minus } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef } from 'react'
import { api, clock, type Live } from '../api'
import { useNow } from '../hooks'

/** 事件处理进度：走到哪一步、此刻在调用什么模型。处理中每秒刷新，结束后放慢。 */
export function LiveProgress({ incidentId }: { incidentId: number }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['live', incidentId],
    queryFn: () => api<Live>(`/incidents/${incidentId}/live`),
    refetchInterval: (q) => (q.state.data?.status === 'resolved' ? 15000 : 1000),
  })
  const now = useNow(data?.running.length ? 100 : 1000)
  // 有新动态时同步刷新时间线、模型调用和事件本身，整个抽屉一起「活」起来
  const seen = useRef<number | null>(null)
  const runSig = useRef('')
  useEffect(() => {
    if (!data) return
    if (seen.current !== null && seen.current !== data.event_count) {
      qc.invalidateQueries({ queryKey: ['events', incidentId] })
      qc.invalidateQueries({ queryKey: ['incidents'] })
      qc.invalidateQueries({ queryKey: ['incident-msgs', incidentId] })
    }
    const sig = data.running.map((r) => r.id).join(',') + '|' + data.event_count
    if (sig !== runSig.current) qc.invalidateQueries({ queryKey: ['incident-calls', incidentId] })
    seen.current = data.event_count
    runSig.current = sig
  }, [data, incidentId, qc])

  if (!data) return <div className="h-[92px] animate-pulse rounded-xl bg-panel-2/60" />
  const active = data.stages.find((s) => s.state === 'active')
  return (
    <section className="rounded-xl bg-panel-2/60 px-4 pt-3.5 pb-3">
      <ol className="flex items-start">
        {data.stages.map((s, n) => (
          <li key={s.key} className="relative flex min-w-0 flex-1 flex-col items-center text-center">
            {n > 0 && (
              <span className="absolute top-[11px] right-1/2 left-[-50%] h-px bg-line-strong">
                <motion.span className="absolute inset-y-0 left-0 bg-accent" initial={false}
                  animate={{ width: s.state === 'done' || s.state === 'active' ? '100%' : '0%' }}
                  transition={{ type: 'spring', stiffness: 120, damping: 22 }} />
              </span>
            )}
            <span className={clsx('relative z-10 grid size-[23px] place-items-center rounded-full text-[11px] font-semibold transition-colors duration-300',
              s.state === 'done' && 'bg-accent text-accent-fg',
              s.state === 'active' && 'bg-panel text-accent ring-2 ring-accent',
              s.state === 'pending' && 'bg-panel text-fg-3 ring-1 ring-line-strong',
              s.state === 'skipped' && 'bg-panel text-fg-3/60 ring-1 ring-line')}>
              {s.state === 'done' ? <Check size={12} strokeWidth={3} />
                : s.state === 'skipped' ? <Minus size={12} />
                  : s.state === 'active' ? <span className="size-2 rounded-full bg-accent"><span className="block size-2 animate-ping rounded-full bg-accent/60" /></span>
                    : n + 1}
            </span>
            <span className={clsx('mt-1.5 truncate px-1 text-[11.5px]', s.state === 'active' ? 'font-medium text-fg' : s.state === 'done' ? 'text-fg-2' : 'text-fg-3')}>{s.label}</span>
            <span className="h-4 truncate px-1 text-[10.5px] text-fg-3 tabular-nums">{clock(s.at)}</span>
          </li>
        ))}
      </ol>
      <AnimatePresence mode="popLayout" initial={false}>
        {data.running.length > 0 ? (
          <motion.div key="run" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2.5 text-[12px]">
            {data.running.map((r) => (
              <span key={r.id} className="inline-flex items-center gap-1.5 text-fg-2">
                <span className="relative flex size-2"><span className="absolute inset-0 animate-ping rounded-full bg-violet/60" /><span className="relative size-2 rounded-full bg-violet" /></span>
                正在调用 <b className="font-medium text-fg">{r.provider === 'jev' ? 'Jev' : 'Claude'}</b>
                · {r.label}{r.step ? ` 第 ${r.step} 轮` : ''}
                <span className="text-fg-3 tabular-nums">{Math.max(0, (now - Date.parse(r.started_at)) / 1000).toFixed(1)}s</span>
              </span>
            ))}
          </motion.div>
        ) : active?.detail || data.latest ? (
          <motion.div key={`d-${active?.key}-${data.event_count}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            className="mt-2 flex items-center gap-2 border-t border-line pt-2.5 text-[12px] text-fg-3">
            {active && <span className="shrink-0 font-medium text-fg-2">{active.label}</span>}
            <span className="truncate">{active?.detail || data.latest?.text.split('\n')[0]}</span>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
}
