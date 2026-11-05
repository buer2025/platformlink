import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Check, CornerDownRight, MessageCircle, ChevronRight, CircleAlert, Inbox, Pencil, RotateCw, Send, Timer, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ago, api, categoryLabel, post, type Group, type Incident, type Message, type Outbound } from '../api'
import { useHotkeys, useListNav, useNow } from '../hooks'
import { Avatar, Badge, Button, Card, Empty, Kbd, listItem, PageHeader, ScoreRing, scoreTone, Segmented, Skeleton, Stat, Textarea, type Tone } from '../ui'

export const purposeLabel: Record<string, string> = {
  ack: '安抚回复', customer_reply: '客户回复', supplier_ticket: '供应商转交',
  supplier_remind: '供应商催办', quota_push: '催量', escalation: '升级人工', manual: '人工消息',
  greeting: '招呼回应', chat: '咨询回复',
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

function TimerPill({ o, now }: { o: Outbound; now: number }) {
  if (o.status !== 'pending' || !o.send_at) return null
  const total = Math.max(1, Date.parse(o.send_at) - Date.parse(o.created_at))
  const left = Math.max(0, Date.parse(o.send_at) - now)
  const r = 6, c = 2 * Math.PI * r
  return (
    <span className="tnum inline-flex h-6 items-center gap-1.5 rounded-full bg-info-soft pr-2.5 pl-1.5 text-[11.5px] font-medium text-info"
      title="倒计时结束后自动发送，期间可修改或取消">
      <svg width={16} height={16} className="-rotate-90">
        <circle cx={8} cy={8} r={r} fill="none" stroke="currentColor" strokeOpacity={0.2} strokeWidth={2} />
        <circle cx={8} cy={8} r={r} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - left / total)} style={{ transition: 'stroke-dashoffset 1s linear' }} />
      </svg>
      {left > 0 ? `${fmtLeft(Math.round(left / 1000))} 后发送` : '发送中'}
    </span>
  )
}

function Row({ o, now, selected, editing, setEditing, onSelect, toCustomer }: {
  o: Outbound; now: number; selected: boolean; editing: boolean; toCustomer: boolean
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
  const long = o.text.length > 150
  const saveEdit = () => text.trim() && act.mutate({ o, action: 'edit', body: { text } }, { onSuccess: () => setEditing(false) })

  return (
    <motion.div layout {...listItem} data-row={o.id} data-testid="outbound" onClick={onSelect}
      className={clsx('group/row relative -mx-3 rounded-2xl px-3 py-4 transition-colors duration-200',
        selected ? 'bg-accent-soft/45' : 'hover:bg-panel-2/60')}>
      <div className="flex gap-3">
        {toCustomer
          ? <span className="grid size-7 shrink-0 place-items-center rounded-full bg-panel-2 text-fg-3 ring-1 ring-line ring-inset"><CornerDownRight size={13} /></span>
          : <Avatar name={o.group_name} size={28} />}
        <div className="min-w-0 flex-1">
          <div className="flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px]">
            {toCustomer
              ? <span className="font-medium text-fg-2">{o.purpose === 'ack' ? '先安抚客户' : o.purpose === 'escalation' ? '拉负责人进来' : '回复客户'}</span>
              : <><span className="text-fg-3">{o.purpose === 'escalation' ? '升级给' : o.purpose === 'quota_push' ? '催量给' : '转交给'}</span><span className="font-medium text-fg">{o.group_name}</span></>}
            {o.purpose === 'ack' && !editing && <span className="truncate text-fg">「{o.text}」</span>}
            {o.mentions.length > 0 && <span className="text-fg-3">{o.mentions.map((m) => `@${m}`).join(' ')}</span>}
            <span className="ml-auto flex items-center gap-2">
              <span className={clsx('tnum text-[11.5px] font-medium', { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad' }[scoreTone(o.score) as 'ok'])}
                title="Agent 对这条消息的准确率评分">准确率 {o.score}</span>
              {o.status === 'pending' ? <TimerPill o={o} now={now} /> : <Badge tone={tone} dot>{label}</Badge>}
            </span>
          </div>
          <AnimatePresence mode="wait" initial={false}>
            {o.purpose === 'ack' && !editing ? null : editing ? (
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
              <motion.div key="text" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-2 max-w-[44rem]">
                <div className={clsx('rounded-2xl rounded-tl-md px-4 py-3 ring-1 ring-line ring-inset',
                  o.purpose.startsWith('supplier') ? 'bg-warn-soft/45' : 'bg-panel-2')}>
                  <p className={clsx('text-[14px] leading-[1.75] whitespace-pre-wrap text-fg', long && !expanded && 'line-clamp-3')}>{o.text}</p>
                </div>
                {long && (
                  <button className="mt-1.5 ml-1 text-xs text-fg-3 hover:text-accent" onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}>
                    {expanded ? '收起' : '展开全文'}
                  </button>
                )}
              </motion.div>
            )}
          </AnimatePresence>
          {o.error && <p className="mt-1.5 flex items-center gap-1 text-xs text-bad"><CircleAlert size={13} />{o.error}</p>}
          {actionable(o) && !editing && (
            <div className={clsx('mt-3 flex flex-wrap gap-1.5 transition-opacity duration-200',
              selected || o.status === 'failed' ? 'opacity-100' : 'md:opacity-0 md:group-hover/row:opacity-100 md:focus-within:opacity-100')}>
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
    </motion.div>
  )
}

/** 客户说了什么：连发的几条都列出来，回复会一次性覆盖它们。 */
function CustomerSaid({ incidentId }: { incidentId: number }) {
  const { data = [] } = useQuery({ queryKey: ['incident-msgs', incidentId], queryFn: () => api<Message[]>(`/incidents/${incidentId}/messages`) })
  const [all, setAll] = useState(false)
  const said = data.filter((m) => !m.from_us)
  if (!said.length) return null
  const shown = all ? said : said.slice(-3)
  return (
    <div className="border-b border-line bg-panel-2/40 px-6 py-3.5">
      <div className="mb-2 flex items-center gap-2 text-[11.5px] text-fg-3">
        <MessageCircle size={12} />客户说{said.length > 1 && <span>· {said.length} 条，回复会一并覆盖</span>}
        {said.length > 3 && <button className="ml-auto hover:text-accent" onClick={() => setAll(!all)}>{all ? '收起' : `查看全部 ${said.length} 条`}</button>}
      </div>
      <ul className="space-y-1.5">
        {shown.map((m) => (
          <li key={m.id} className="flex items-baseline gap-2.5 text-[13.5px]">
            <span className="tnum w-10 shrink-0 text-[11px] text-fg-3">{new Date(m.created_at).toTimeString().slice(0, 5)}</span>
            <span className="shrink-0 text-fg-3">{m.sender}</span>
            <span className="text-fg">{m.text}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const verdictText: Record<string, [string, Tone]> = {
  customer: ['客户侧问题', 'info'], us: ['我方问题', 'violet'], supplier: ['供应商问题', 'warn'], unknown: ['待确认', 'slate'],
}

/** 一个事件下的所有待发消息放在一张卡片里：先看是什么事、Agent 怎么判断，再看要发什么。 */
function Thread({ incident, items, groupName, children }: { incident?: Incident; items: Outbound[]; groupName: string; children: React.ReactNode }) {
  const first = items[0]
  const [vt, vtone] = incident ? verdictText[incident.responsible] ?? ['排查中', 'slate' as Tone] : ['', 'slate' as Tone]
  return (
    <motion.section layout {...listItem}>
      <Card className="overflow-hidden">
        <header className="flex items-start gap-3.5 border-b border-line px-6 pt-5 pb-4">
          <Avatar name={groupName} size={40} square />
          <div className="min-w-0 flex-1">
            <h3 className="line-clamp-2 text-[15.5px] leading-snug font-semibold tracking-tight text-fg">
              {incident?.summary || (first.purpose === 'quota_push' ? '找供应商要量' : '主动消息')}
            </h3>
            <p className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[12.5px] text-fg-3">
              <span className="text-fg-2">{groupName}</span>
              {incident && <><span className="opacity-50">/</span><span>{categoryLabel[incident.category] ?? incident.category}</span></>}
              {incident?.model && <><span className="opacity-50">/</span><span>{incident.model}</span></>}
              <span className="opacity-50">/</span><span>{ago(first.created_at)}</span>
            </p>
          </div>
          {incident && (
            <Link to={`/incidents?open=${incident.id}`} title="查看排查过程"
              className="flex shrink-0 items-center gap-2 rounded-xl py-1 pr-1.5 pl-2.5 transition-colors hover:bg-panel-2">
              <span className="text-right">
                <span className="block text-[10.5px] tracking-wider text-fg-3">AGENT 判断</span>
                <Badge tone={vtone} className="mt-0.5">{vt}</Badge>
              </span>
              <ScoreRing score={incident.confidence} size={32} />
              <ChevronRight size={14} className="text-fg-3" />
            </Link>
          )}
        </header>
        {incident && <CustomerSaid incidentId={incident.id} />}
        <div className="divide-y divide-line px-6 py-1">{children}</div>
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
  const { data: groupList = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const groupNames = new Map(groupList.map((g) => [g.id, g.name]))
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

  const approval = open.filter((o) => o.status === 'needs_approval' || o.status === 'failed').length
  const counting = open.filter((o) => o.status === 'pending').length
  const today = new Date(now).toDateString()
  const sentToday = done.filter((o) => o.status === 'sent' && new Date(o.created_at).toDateString() === today).length
  return (
    <div>
      <PageHeader title="预发队列" desc="Agent 起草的每条消息都先到这里。你可以修改、立即发送或取消；不操作的会在倒计时结束后自动发出。">
        <Segmented value={tab} onChange={(v) => { setTab(v); setSel(null) }} options={[
          { value: 'open', label: <>待处理 <span className="tabular-nums text-fg-3">{open.length}</span></> },
          { value: 'done', label: '历史' },
        ]} />
      </PageHeader>
      <div className="mb-8 grid grid-cols-3 gap-3">
        <Stat label="需要你确认" value={approval} tone="warn" icon={<CircleAlert size={13} />} hint="评分不足或链路要求人工" onClick={() => setTab('open')} />
        <Stat label="倒计时中" value={counting} tone="info" icon={<Timer size={13} />} hint="到点自动发送" onClick={() => setTab('open')} />
        <Stat label="今日已发送" value={sentToday} tone="ok" icon={<Check size={13} />} hint="含自动与手动" onClick={() => setTab('done')} />
      </div>
      <h2 className="sr-only">{tab === 'open' ? `待处理（${open.length}）` : '历史'}</h2>
      {isLoading ? <Skeleton /> : rows.length === 0 ? (
        tab === 'open'
          ? <Empty icon={<Check size={18} />} title="全部处理完了">新消息进来时会出现在这里，按 <Kbd>J</Kbd><Kbd>K</Kbd> 上下切换，<Kbd>⌘</Kbd><Kbd>↵</Kbd> 发送。</Empty>
          : <Empty icon={<Inbox size={18} />} title="还没有历史消息" />
      ) : (
        <div className={clsx('space-y-5', tab === 'done' && 'opacity-85')}>
          <AnimatePresence initial={false} mode="popLayout">
            {threads.map(([key, items]) => (
              <Thread key={key} items={items} groupName={threadGroup(items, incidents, groupNames)} incident={items[0].incident_id ? incidents.get(items[0].incident_id) : undefined}>
                <AnimatePresence initial={false} mode="popLayout">
                  {items.map((o) => (
                    <Row key={o.id} toCustomer={!['supplier_ticket', 'supplier_remind', 'quota_push'].includes(o.purpose) && o.group_name === threadGroup(items, incidents, groupNames)} o={o} now={now} selected={sel === o.id} editing={editing === o.id}
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

/** 卡片标题用客户群的名字（转交单发往供应商群，但事件属于客户）。 */
function threadGroup(items: Outbound[], incidents: Map<number, Incident>, names: Map<number, string>) {
  const inc = items[0].incident_id ? incidents.get(items[0].incident_id) : undefined
  return (inc && names.get(inc.group_id)) || items[0].group_name
}
