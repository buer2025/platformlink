import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { AtSign, Check, ChevronRight, CircleAlert, Inbox, Pencil, RotateCw, Send, Timer, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ago, api, categoryLabel, post, type Incident, type Outbound } from '../api'
import { useHotkeys, useListNav, useNow } from '../hooks'
import { Badge, Button, Card, Empty, Kbd, listItem, PageHeader, ScoreRing, Segmented, Skeleton, Textarea, type Tone } from '../ui'

export const purposeLabel: Record<string, string> = {
  ack: '安抚回复', customer_reply: '客户回复', supplier_ticket: '供应商转交',
  supplier_remind: '供应商催办', quota_push: '催量',
}
const statusInfo: Record<Outbound['status'], [string, Tone]> = {
  pending: ['倒计时', 'info'], needs_approval: ['待确认', 'warn'], sending: ['发送中', 'info'],
  sent: ['已发送', 'ok'], cancelled: ['已取消', 'slate'], failed: ['发送失败', 'bad'],
}
const OPEN: Outbound['status'][] = ['pending', 'needs_approval', 'failed', 'sending']
const actionable = (o: Outbound) => ['pending', 'needs_approval', 'failed'].includes(o.status)

function useActions() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ o, action, body }: { o: Outbound; action: 'send' | 'cancel' | 'edit'; body?: unknown }) =>
      post<Outbound>(`/outbound/${o.id}/${action}`, body),
    onMutate: async ({ o, action }) => {
      if (action === 'edit') return
      await qc.cancelQueries({ queryKey: ['outbound'] })
      const prev = qc.getQueryData<Outbound[]>(['outbound'])
      // 乐观更新：立刻从待处理移走，失败再回滚
      qc.setQueryData<Outbound[]>(['outbound'], (rows) => rows?.map((r) => r.id === o.id
        ? { ...r, status: action === 'send' ? 'sending' : 'cancelled' } : r))
      return { prev }
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(['outbound'], ctx.prev)
      toast.error(e instanceof Error ? e.message : '操作失败')
    },
    onSuccess: (r, { action }) => {
      if (action === 'send') {
        if (r.status === 'sent') toast.success(`已发送到 ${r.group_name}`)
        else toast.error(`发送失败：${r.error}`)
      } else if (action === 'cancel') toast('已取消', { description: r.text.slice(0, 40) })
      else toast.success('已保存修改，确认后发送')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['outbound'] }),
  })
}

function Countdown({ o, now }: { o: Outbound; now: number }) {
  if (o.status !== 'pending' || !o.send_at) return null
  const total = Math.max(1, Date.parse(o.send_at) - Date.parse(o.created_at))
  const left = Math.max(0, Date.parse(o.send_at) - now)
  return (
    <div className="absolute inset-x-2 bottom-0 h-[2px] overflow-hidden rounded-full bg-line/50">
      <motion.div className="h-full bg-info" animate={{ width: `${(left / total) * 100}%` }} transition={{ duration: 1, ease: 'linear' }} />
    </div>
  )
}

function Row({ o, now, selected, editing, setEditing, onSelect }: {
  o: Outbound; now: number; selected: boolean; editing: boolean
  setEditing: (v: boolean) => void; onSelect: () => void
}) {
  const act = useActions()
  const [text, setText] = useState(o.text)
  const [expanded, setExpanded] = useState(false)
  const ta = useRef<HTMLTextAreaElement>(null)
  const [wasEditing, setWasEditing] = useState(editing)
  if (editing !== wasEditing) { setWasEditing(editing); if (editing) setText(o.text) }
  useEffect(() => { if (editing) requestAnimationFrame(() => ta.current?.focus()) }, [editing])
  const [label, tone] = statusInfo[o.status]
  const left = o.send_at ? Math.max(0, Math.round((Date.parse(o.send_at) - now) / 1000)) : null
  const long = o.text.length > 120
  const saveEdit = () => text.trim() && act.mutate({ o, action: 'edit', body: { text } }, { onSuccess: () => setEditing(false) })

  return (
    <motion.div layout {...listItem} data-row={o.id} data-testid="outbound" onClick={onSelect}
      className={clsx('group/row relative -mx-2 rounded-xl px-2 py-3 transition-colors duration-150',
        selected ? 'bg-accent-soft/50' : 'hover:bg-panel-2/70')}>
      <div className="flex gap-3">
        <ScoreRing score={o.score} size={30} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs text-fg-3">
            <span className="font-medium text-fg-2">{purposeLabel[o.purpose] ?? o.purpose}</span>
            <span>→ {o.group_name}</span>
            {o.mentions.length > 0 && <span className="flex items-center gap-0.5"><AtSign size={11} />{o.mentions.map((m) => `@${m}`).join(' ')}</span>}
            <span className="ml-auto flex items-center gap-1.5 tabular-nums">
              {o.status === 'pending' && left !== null
                ? <span className="flex items-center gap-1 font-medium text-info"><Timer size={12} />{left > 0 ? `${fmtLeft(left)} 后自动发送` : '即将发送'}</span>
                : <Badge tone={tone} dot>{label}</Badge>}
            </span>
          </div>
          <AnimatePresence mode="wait" initial={false}>
            {editing ? (
              <motion.div key="edit" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="mt-2">
                <Textarea ref={ta} rows={4} value={text} onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveEdit() }
                    if (e.key === 'Escape') { e.stopPropagation(); setEditing(false) }
                  }} />
                <div className="mt-2 flex items-center gap-2">
                  <Button variant="primary" size="sm" disabled={act.isPending || !text.trim()} onClick={saveEdit} kbd="⌘↵">保存（之后需确认发送）</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消编辑</Button>
                </div>
              </motion.div>
            ) : (
              <motion.div key="text" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-1.5">
                <p className={clsx('text-[14px] leading-7 whitespace-pre-wrap text-fg', long && !expanded && 'line-clamp-3')}>{o.text}</p>
                {long && (
                  <button className="mt-0.5 text-xs text-fg-3 hover:text-accent" onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}>
                    {expanded ? '收起' : '展开全文'}
                  </button>
                )}
              </motion.div>
            )}
          </AnimatePresence>
          {o.error && <p className="mt-1.5 flex items-center gap-1 text-xs text-bad"><CircleAlert size={13} />{o.error}</p>}
          {actionable(o) && !editing && (
            <div className={clsx('mt-2.5 flex flex-wrap gap-1.5 transition-opacity duration-150',
              selected || o.status === 'needs_approval' || o.status === 'failed' ? 'opacity-100' : 'md:opacity-0 md:group-hover/row:opacity-100 md:focus-within:opacity-100')}>
              <Button variant="primary" size="sm" disabled={act.isPending} icon={o.status === 'failed' ? <RotateCw size={13} /> : <Send size={13} />}
                onClick={(e) => { e.stopPropagation(); act.mutate({ o, action: 'send' }) }} kbd={selected ? '⌘↵' : undefined}>
                {o.status === 'failed' ? '重新发送' : '立即发送'}
              </Button>
              <Button size="sm" variant="ghost" icon={<Pencil size={13} />} onClick={(e) => { e.stopPropagation(); setEditing(true) }} kbd={selected ? 'E' : undefined}>修改</Button>
              <Button size="sm" variant="ghost" className="text-fg-3 hover:text-bad" icon={<X size={13} />} disabled={act.isPending}
                onClick={(e) => { e.stopPropagation(); if (confirm('确定取消这条消息？')) act.mutate({ o, action: 'cancel' }) }}>取消</Button>
            </div>
          )}
        </div>
      </div>
      <Countdown o={o} now={now} />
    </motion.div>
  )
}

/** 一个事件下的所有待发消息放在一张卡片里：先看是什么事，再看要发什么。 */
function Thread({ incident, items, children }: { incident?: Incident; items: Outbound[]; children: React.ReactNode }) {
  const first = items[0]
  return (
    <motion.section layout {...listItem}>
      <Card className="px-5 pt-4 pb-2">
        <header className="mb-1 flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[15px] font-semibold tracking-tight">
              {incident?.summary || first.group_name}
            </h3>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-fg-3">
              {incident && <span>{first.group_name}</span>}
              {incident && <span>·</span>}
              {incident && <span>{categoryLabel[incident.category] ?? incident.category}</span>}
              {incident?.model && <><span>·</span><span>{incident.model}</span></>}
              <span>·</span><span>{ago(first.created_at)}</span>
            </p>
          </div>
          {incident && (
            <Link to={`/incidents?open=${incident.id}`} className="flex shrink-0 items-center gap-0.5 text-xs text-fg-3 transition-colors hover:text-accent">
              事件 #{incident.id}<ChevronRight size={13} />
            </Link>
          )}
        </header>
        <div className="divide-y divide-line/70">{children}</div>
      </Card>
    </motion.section>
  )
}

const fmtLeft = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}s`)

export default function Presend() {
  const now = useNow()
  const [tab, setTab] = useState<'open' | 'done'>('open')
  const [editing, setEditing] = useState<number | null>(null)
  const { data = [], isLoading } = useQuery({ queryKey: ['outbound'], queryFn: () => api<Outbound[]>('/outbound'), refetchInterval: 3000 })
  const open = data.filter((o) => OPEN.includes(o.status))
    .sort((a, b) => rank(a) - rank(b) || a.id - b.id)
  const done = data.filter((o) => !OPEN.includes(o.status))
  const { data: incidentList = [] } = useQuery({ queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents') })
  const incidents = new Map(incidentList.map((i) => [i.id, i]))
  const threads = groupBy(tab === 'open' ? open : done)
  const rows = threads.flatMap(([, items]) => items)  // 键盘导航按显示顺序
  const { sel, setSel, current, move } = useListNav(rows)
  const act = useActions()

  useHotkeys({
    j: () => move(1), arrowdown: () => move(1),
    k: () => move(-1), arrowup: () => move(-1),
    e: () => current && actionable(current) && setEditing(current.id),
    'mod+enter': () => current && actionable(current) && act.mutate({ o: current, action: 'send' }),
    '1': () => setTab('open'), '2': () => setTab('done'),
  }, editing === null)

  const approval = open.filter((o) => o.status === 'needs_approval').length
  return (
    <div>
      <PageHeader title="预发队列"
        desc={open.length ? `${open.length} 条待处理${approval ? `，其中 ${approval} 条需要你确认` : ''}。不操作的消息会在倒计时结束后自动发出。` : 'Agent 起草的消息都会先进入这里。'}>
        <Segmented value={tab} onChange={(v) => { setTab(v); setSel(null) }} options={[
          { value: 'open', label: <>待处理 <span className="tabular-nums text-fg-3">{open.length}</span></> },
          { value: 'done', label: '历史' },
        ]} />
      </PageHeader>
      <h2 className="sr-only">{tab === 'open' ? `待处理（${open.length}）` : '历史'}</h2>
      {isLoading ? <Skeleton /> : rows.length === 0 ? (
        tab === 'open'
          ? <Empty icon={<Check size={18} />} title="全部处理完了">新消息进来时会出现在这里，按 <Kbd>J</Kbd><Kbd>K</Kbd> 上下切换，<Kbd>⌘</Kbd><Kbd>↵</Kbd> 发送。</Empty>
          : <Empty icon={<Inbox size={18} />} title="还没有历史消息" />
      ) : (
        <div className={clsx('space-y-4', tab === 'done' && 'opacity-80')}>
          <AnimatePresence initial={false} mode="popLayout">
            {threads.map(([key, items]) => (
              <Thread key={key} items={items} incident={items[0].incident_id ? incidents.get(items[0].incident_id) : undefined}>
                <AnimatePresence initial={false} mode="popLayout">
                  {items.map((o) => (
                    <Row key={o.id} o={o} now={now} selected={sel === o.id} editing={editing === o.id}
                      setEditing={(v) => setEditing(v ? o.id : null)} onSelect={() => setSel(o.id)} />
                  ))}
                </AnimatePresence>
              </Thread>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

const rank = (o: Outbound) => ({ failed: 0, needs_approval: 1, pending: 2, sending: 3 } as Record<string, number>)[o.status] ?? 9

function groupBy(list: Outbound[]): [string, Outbound[]][] {
  const m = new Map<string, Outbound[]>()
  for (const o of list) {
    const k = o.incident_id ? `i${o.incident_id}` : `o${o.id}`
    m.set(k, [...(m.get(k) ?? []), o])
  }
  return [...m.entries()]
}
