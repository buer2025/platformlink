import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  BookPlus, Bot, Minus, CornerUpLeft, Check, ChevronRight, CircleAlert, Database, GitMerge, Lightbulb, MessageCircle,
  NotebookPen, Pencil, UserRound, type LucideIcon, RotateCcw, Send, ShieldCheck, Sparkles, Timer, Wrench, X, Zap,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import {
  ago, api, categoryLabel, fmtTime, patch, post, respLabel,
  type Group, type Incident, type IncidentEvent, type Knowledge, type Me, type Message, type User, type Workflow,
} from '../api'
import { useHotkeys, useListNav, useNow } from '../hooks'
import {
  Avatar, Badge, Button, Card, Drawer, Empty, Input, Kbd, Select, listItem, PageHeader, ScoreRing, Segmented, Skeleton, type Tone,
} from '../ui'
import { Composer } from '../components/Composer'
import { KnowledgeEditor } from './Knowledge'
import { LiveProgress } from '../components/LiveProgress'
import { IncidentCalls, useCallCount } from '../components/ModelCalls'

const statusLabel: Record<Incident['status'], [string, Tone]> = {
  open: ['新建', 'slate'], investigating: ['排查中', 'info'], waiting_supplier: ['等待供应商', 'warn'], resolved: ['已解决', 'ok'],
}

const eventMeta: Record<string, [string, LucideIcon, Tone]> = {
  created: ['客户反馈', MessageCircle, 'slate'],
  merged: ['追加反馈', GitMerge, 'slate'],
  classified: ['分诊', Sparkles, 'violet'],
  lookup: ['日志预查询', Database, 'info'],
  knowledge: ['命中知识', Lightbulb, 'warn'],
  tool: ['Agent 调用工具', Wrench, 'info'],
  finding: ['Agent 结论', Bot, 'accent' as Tone],
  queued: ['进入预发', Timer, 'slate'],
  sent: ['已发送', Send, 'ok'],
  send_failed: ['发送失败', CircleAlert, 'bad'],
  edited: ['人工修改', Pencil, 'warn'],
  cancelled: ['已取消', X, 'slate'],
  supplier_reply: ['供应商回复', MessageCircle, 'warn'],
  no_supplier: ['需人工转交', CircleAlert, 'bad'],
  manual: ['人工操作', Pencil, 'slate'],
  reinvestigate: ['重新排查', RotateCcw, 'violet'],
  integrated: ['整合回复', GitMerge, 'violet'],
  followup: ['跟进回复', MessageCircle, 'accent' as Tone],
  no_reply: ['无需回复', Check, 'slate'],
  queued_for_integration: ['等待整合', Timer, 'slate'],
  quoted: ['引用追问', MessageCircle, 'slate'],
  reopened: ['重新打开', RotateCcw, 'warn'],
  escalated: ['升级人工', CircleAlert, 'bad'],
  auto_closed: ['自动关闭', Timer, 'slate'],
  memory_saved: ['沉淀记忆', Lightbulb, 'ok'],
  human_takeover: ['人工接管', Pencil, 'info'],
  quota_push: ['起草催量', Send, 'warn'],
  sla: ['等待超时', Timer, 'bad'],
  assigned: ['指派', UserRound, 'accent' as Tone],
  approved: ['确认发送', Check, 'ok'],
  error: ['处理出错', CircleAlert, 'bad'],
  note: ['备注', NotebookPen, 'slate'],
  compressed: ['压缩对话', GitMerge, 'slate'],
  knowledge_saved: ['沉淀知识', BookPlus, 'ok'],
  evidence: ['证据清单', ShieldCheck, 'info'],
}

const toneColor: Record<string, string> = {
  slate: 'text-fg-3 bg-panel-2', info: 'text-info bg-info-soft', ok: 'text-ok bg-ok-soft', warn: 'text-warn bg-warn-soft',
  bad: 'text-bad bg-bad-soft', violet: 'text-violet bg-violet-soft', accent: 'text-accent bg-accent-soft',
}

function TimelineItem({ e, last }: { e: IncidentEvent; last: boolean }) {
  const [label, Icon, tone] = eventMeta[e.kind] ?? [e.kind, Zap, 'slate' as Tone]
  const long = e.kind === 'tool' || e.kind === 'lookup' || e.text.length > 220
  const [open, setOpen] = useState(e.kind === 'finding')
  const [head, ...rest] = e.text.split('\n')
  return (
    <motion.li initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} className="relative flex gap-3 pb-4">
      {!last && <span className="absolute top-7 bottom-0 left-[13px] w-px bg-line" />}
      <span className={clsx('relative grid size-[27px] shrink-0 place-items-center rounded-full', toneColor[tone])}><Icon size={13} /></span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-medium text-fg">{label}</span>
          {e.actor !== 'agent' && <span className="text-fg-3">{e.actor}</span>}
          <span className="ml-auto text-fg-3 tabular-nums" title={fmtTime(e.created_at)}>{fmtTime(e.created_at).slice(5)}</span>
        </div>
        {long ? (
          <>
            <button onClick={() => setOpen(!open)} className="mt-1 flex w-full items-start gap-1 text-left text-[13px] text-fg-2 hover:text-fg">
              <ChevronRight size={14} className={clsx('mt-0.5 shrink-0 transition-transform duration-200', open && 'rotate-90')} />
              <span className={clsx(e.kind === 'tool' && 'font-mono text-xs', !open && 'line-clamp-1')}>{head}</span>
            </button>
            <AnimatePresence initial={false}>
              {open && rest.length > 0 && (
                <motion.pre initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                  className="mt-1.5 overflow-hidden rounded-lg border border-line bg-panel-2 p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-fg-2">
                  {rest.join('\n')}
                </motion.pre>
              )}
            </AnimatePresence>
          </>
        ) : <p className="mt-0.5 text-[13px] whitespace-pre-wrap text-fg-2">{e.text}</p>}
      </div>
    </motion.li>
  )
}

/** 负责人：认领 / 指派。 */
function Assignee({ incident }: { incident: Incident }) {
  const qc = useQueryClient()
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
  const set = useMutation({
    mutationFn: (id: number | null) => patch<Incident>(`/incidents/${incident.id}`, { assignee_id: id }),
    onSuccess: (r) => toast.success(r.assignee_id ? `已指派给 ${users.find((u) => u.id === r.assignee_id)?.display_name}` : '已取消指派'),
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['incidents'] }); qc.invalidateQueries({ queryKey: ['events', incident.id] }) },
  })
  const cur = users.find((u) => u.id === incident.assignee_id)
  return (
    <div className="flex items-center gap-3 rounded-xl bg-panel-2/60 px-4 py-2.5">
      {cur ? <Avatar name={cur.display_name} size={26} /> : <span className="grid size-[26px] place-items-center rounded-full border border-dashed border-line-strong text-fg-3"><UserRound size={13} /></span>}
      <div className="min-w-0 flex-1 text-[13px]">
        <div className="text-[11px] text-fg-3">负责人</div>
        <div className="font-medium">{cur?.display_name ?? '未指派'}</div>
      </div>
      {me?.id && incident.assignee_id !== me.id && (
        <Button size="sm" variant="primary" onClick={() => set.mutate(me.id)}>认领</Button>
      )}
      <Select className="w-32" value={incident.assignee_id ?? ''} onChange={(e) => set.mutate(e.target.value ? Number(e.target.value) : null)}>
        <option value="">未指派</option>
        {users.filter((u) => u.active).map((u) => <option key={u.id} value={u.id}>{u.display_name}{u.on_duty ? ' · 值班' : ''}</option>)}
      </Select>
    </div>
  )
}

const verdict = (i: Incident) => ({
  customer: '客户侧问题', us: '我方问题', supplier: '供应商问题', unknown: '证据不足，待确认',
} as Record<string, string>)[i.responsible] ?? '排查中…'

const scoreRule = (n: number) => n >= 90
  ? '准确率 ≥ 90：回复倒计时 2 分钟后自动发出'
  : n >= 70 ? '准确率 70–89：回复倒计时 10 分钟后自动发出，期间可改可撤'
    : '准确率 < 70：证据不足，回复必须人工确认'

function Evidence({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 160
  return (
    <section>
      <p className={clsx('text-[13.5px] leading-7 whitespace-pre-wrap text-fg-2', long && !open && 'line-clamp-4')}>{text}</p>
      {long && <button className="mt-1 text-xs text-fg-3 hover:text-accent" onClick={() => setOpen(!open)}>{open ? '收起' : '展开依据'}</button>}
    </section>
  )
}

/** 证据清单：哪些已证实、哪些矛盾、还缺什么 —— 准确率为什么是这个数。 */
function EvidenceChecks({ checks }: { checks: Incident['evidence_checks'] }) {
  if (!checks?.length) return null
  const meta = {
    ok: ['text-ok bg-ok-soft', Check, '已证实'],
    warn: ['text-warn bg-warn-soft', CircleAlert, '有矛盾'],
    missing: ['text-fg-3 bg-panel-2', Minus, '缺失'],
  } as const
  const n = (s: string) => checks.filter((c) => c.state === s).length
  return (
    <section>
      <div className="mb-2 flex items-baseline gap-3">
        <h3 className="text-[13px] font-medium">证据清单</h3>
        <span className="text-xs text-fg-3 tabular-nums">已证实 {n('ok')} · 矛盾 {n('warn')} · 缺失 {n('missing')}</span>
      </div>
      <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
        {checks.map((c) => {
          const [cls, Icon, word] = meta[c.state]
          return (
            <li key={c.key} className="flex items-start gap-3 px-3.5 py-2.5">
              <span title={word} className={clsx('mt-px grid size-5 shrink-0 place-items-center rounded-full', cls)}><Icon size={11} strokeWidth={2.5} /></span>
              <span className="w-[5.5em] shrink-0 text-[13px] font-medium text-fg-2">{c.label}</span>
              <span className={clsx('min-w-0 text-[13px] leading-relaxed', c.state === 'missing' ? 'text-fg-3' : 'text-fg-2')}>{c.detail}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function Fact({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-fg-3">{label}</dt>
      <dd className={clsx('mt-0.5 truncate text-[13px] text-fg-2', mono && 'font-mono text-xs')}>{children || '—'}</dd>
    </div>
  )
}

function IncidentDrawer({ i, groups, workflows, onClose }: {
  i: Incident | undefined; groups: Map<number, Group>; workflows: Map<number, Workflow>; onClose: () => void
}) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const [tab, setTab] = useState<'timeline' | 'messages' | 'calls'>('timeline')
  const [draft, setDraft] = useState<Partial<Knowledge> | null>(null)
  const [quote, setQuote] = useState<Message | null>(null)
  const now = useNow(30000)
  const id = i?.id
  const { data: events = [] } = useQuery({ queryKey: ['events', id], queryFn: () => api<IncidentEvent[]>(`/incidents/${id}/events`), enabled: !!id, refetchInterval: 15000 })
  const callCount = useCallCount(id)
  const { data: msgs = [] } = useQuery({ queryKey: ['incident-msgs', id], queryFn: () => api<Message[]>(`/incidents/${id}/messages`), enabled: !!id })
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => patch<Incident>(`/incidents/${id}`, body),
    onSuccess: (_r, body) => {
      if (body.status === 'resolved') toast.success('已标记解决', { description: '处理经验可以一键沉淀到知识库', action: { label: '沉淀知识', onClick: () => distill.mutate() } })
      if (body.note) setNote('')
    },
    onError: (e) => toast.error(String(e instanceof Error ? e.message : e)),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['incidents'] }); qc.invalidateQueries({ queryKey: ['events', id] }) },
  })
  const distill = useMutation({
    mutationFn: () => post<Partial<Knowledge>>(`/incidents/${id}/knowledge-draft`),
    onSuccess: setDraft,
    onError: (e) => toast.error(String(e instanceof Error ? e.message : e)),
  })
  useHotkeys({ 'drawer:r': () => i && i.status !== 'resolved' && update.mutate({ status: 'resolved' }) }, !!i && !draft)

  if (!i) return <Drawer open={false} onClose={onClose} title=""><span /></Drawer>
  const [sl, st] = statusLabel[i.status]
  return (
    <>
      <Drawer open onClose={onClose} width={620}
        title={<span className="flex items-center gap-2"><span className="text-fg-3">#{i.id}</span>{i.summary || '事件'}</span>}
        subtitle={<span className="flex flex-wrap items-center gap-1.5">
          <Badge tone={st} dot>{sl}</Badge>
          {i.workflow_id && <Badge tone="violet">链路 · {workflows.get(i.workflow_id)?.name}</Badge>}
          <span>{groups.get(i.group_id)?.name} · {categoryLabel[i.category] ?? i.category} · {ago(i.created_at)}</span>
        </span>}
        footer={<>
          {i.status !== 'resolved'
            ? <Button variant="primary" icon={<Check size={14} />} onClick={() => update.mutate({ status: 'resolved' })} kbd="R">标记已解决</Button>
            : <Button icon={<RotateCcw size={14} />} onClick={() => update.mutate({ status: 'investigating' })}>重新打开</Button>}
          <Button icon={<BookPlus size={14} />} disabled={distill.isPending} onClick={() => distill.mutate()}>
            {distill.isPending ? '正在总结…' : '沉淀为知识'}
          </Button>
        </>}>
        <div className="space-y-6">
          {/* 第零层：进度 —— 现在走到哪一步、正在调用什么 */}
          <LiveProgress incidentId={i.id} />
          {/* 第一层：结论 —— 一眼知道是谁的问题、有多大把握 */}
          <div className="flex items-center gap-4">
            <ScoreRing score={i.confidence} size={52} />
            <div className="min-w-0">
              <div className="text-[11px] tracking-wide text-fg-3">AGENT 判断</div>
              <div className="mt-0.5 text-[17px] font-semibold tracking-tight">{verdict(i)}</div>
              <div className="mt-0.5 text-xs text-fg-3">{scoreRule(i.confidence)}</div>
              {i.human_until && Date.parse(i.human_until) > now && (
                <div className="mt-1.5"><Badge tone="info" dot>{i.human_by || '同事'} 正在人工处理 · AI 暂停自动发送</Badge></div>
              )}
              {i.escalated_at && <div className="mt-1.5"><Badge tone="bad" dot>已升级人工 · {i.escalation_reason}</Badge></div>}
            </div>
          </div>
          {/* 第二层：依据（默认折叠到 4 行） */}
          {i.evidence && <Evidence text={i.evidence} />}
          <EvidenceChecks checks={i.evidence_checks} />
          <Assignee incident={i} />
          {/* 第三层：事实，弱化展示 */}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-xl bg-panel-2/60 px-4 py-3.5 sm:grid-cols-3">
            <Fact label="request_id" mono>{i.request_id}</Fact>
            <Fact label="模型">{i.model}</Fact>
            <Fact label="供应商群">{i.supplier_group_id ? groups.get(i.supplier_group_id)?.name : ''}</Fact>
            <Fact label="转交时间">{i.ticket_sent_at && fmtTime(i.ticket_sent_at).slice(5)}</Fact>
            <Fact label="供应商回复">{i.supplier_replied_at && fmtTime(i.supplier_replied_at).slice(5)}</Fact>
            <Fact label="催办 / 合并">{`${i.reminders} 次 / ${i.related_messages} 条`}</Fact>
          </dl>
          <Segmented value={tab} onChange={setTab} options={[
            { value: 'timeline', label: <>处理过程 <span className="text-fg-3 tabular-nums">{events.length}</span></> },
            { value: 'messages', label: <>相关消息 <span className="text-fg-3 tabular-nums">{msgs.length}</span></> },
            { value: 'calls', label: <>模型调用 <span className="text-fg-3 tabular-nums">{callCount}</span></> },
          ]} />
          {tab === 'timeline' ? (
            <ol>{events.map((e, n) => <TimelineItem key={e.id} e={e} last={n === events.length - 1} />)}</ol>
          ) : tab === 'calls' ? <IncidentCalls incidentId={i.id} /> : (
            <ul className="space-y-2.5">
              {msgs.map((m) => (
                <li key={m.id} className={clsx('group/msg flex items-center gap-1.5', m.from_us && 'flex-row-reverse')}>
                  <div className={clsx('max-w-[85%] rounded-2xl px-3 py-2 text-[13px]', m.from_us ? 'rounded-br-md bg-accent text-accent-fg' : 'rounded-bl-md bg-panel-2')}>
                    <div className={clsx('mb-0.5 text-[11px]', m.from_us ? 'text-white/70' : 'text-fg-3')}>{groups.get(m.group_id)?.name} · {m.sender} · {fmtTime(m.created_at).slice(5)}</div>
                    <div className="whitespace-pre-wrap">{m.text}</div>
                  </div>
                  {!m.from_us && m.group_id === i.group_id && (
                    <button title="引用这条回复" onClick={() => setQuote(m)}
                      className="grid size-7 place-items-center rounded-full text-fg-3 opacity-0 transition-opacity group-hover/msg:opacity-100 hover:bg-panel-2 hover:text-accent">
                      <CornerUpLeft size={14} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <Composer groupId={i.group_id} groupName={groups.get(i.group_id)?.name ?? ''} incidentId={i.id}
            quote={quote} onClearQuote={() => setQuote(null)} />
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (note.trim()) update.mutate({ note }) }}>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="内部备注（客户看不到），记入处理过程…" />
            <Button type="submit" disabled={!note.trim()}>记录</Button>
          </form>
        </div>
      </Drawer>
      <KnowledgeEditor value={draft} onClose={() => setDraft(null)} />
    </>
  )
}

type Filter = 'mine' | 'active' | 'waiting_supplier' | 'resolved' | 'all'

export default function Incidents() {
  const now = useNow(15000)
  const [params, setParams] = useSearchParams()
  const [filter, setFilter] = useState<Filter>('active')
  const { data = [], isLoading } = useQuery({ queryKey: ['incidents'], queryFn: () => api<Incident[]>('/incidents') })
  const { data: groupList = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const { data: wfList = [] } = useQuery({ queryKey: ['workflows'], queryFn: () => api<Workflow[]>('/workflows') })
  const groups = new Map(groupList.map((g) => [g.id, g]))
  const workflows = new Map(wfList.map((w) => [w.id, w]))
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
  const { data: userList = [] } = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })
  const users = new Map(userList.map((u) => [u.id, u]))
  const match = (i: Incident, f: Filter) => f === 'all' ? true : f === 'mine' ? i.assignee_id === me?.id && i.status !== 'resolved' : f === 'active' ? i.status !== 'resolved' : i.status === f
  const rows = data.filter((i) => match(i, filter))
  const openId = Number(params.get('open')) || null
  const setOpen = (id: number | null) => setParams(id ? { open: String(id) } : {}, { replace: true })
  const { sel, setSel, current, move } = useListNav(rows)
  useHotkeys({
    j: () => move(1), k: () => move(-1), arrowdown: () => move(1), arrowup: () => move(-1),
    enter: () => current && setOpen(current.id),
  }, !openId)

  const count = (f: Filter) => data.filter((i) => match(i, f)).length
  return (
    <div>
      <PageHeader title="事件中心" desc="每个客户问题从反馈、排查、转交到解决的完整记录。">
        <Segmented value={filter} onChange={setFilter} options={[
          ...(me?.id ? [{ value: 'mine' as Filter, label: <>我的 <span className="text-fg-3 tabular-nums">{count('mine')}</span></> }] : []),
          { value: 'active', label: <>进行中 <span className="text-fg-3 tabular-nums">{count('active')}</span></> },
          { value: 'waiting_supplier', label: '等供应商' },
          { value: 'resolved', label: '已解决' },
          { value: 'all', label: '全部' },
        ]} />
      </PageHeader>
      {isLoading ? <Skeleton /> : rows.length === 0 ? <Empty icon={<Zap size={18} />} title="暂无事件">客户在群里反馈问题后，Agent 会自动建立事件并开始排查。</Empty> : (
        <Card className="divide-y divide-line overflow-hidden">
          <AnimatePresence initial={false}>
            {rows.map((i) => {
              const [rl, rt] = respLabel[i.responsible] ?? [i.responsible || '—', 'slate']
              const [sl, st] = statusLabel[i.status]
              const overdue = i.status === 'waiting_supplier' && !i.supplier_replied_at && i.reminders > 0
              return (
                <motion.button layout {...listItem} key={i.id} data-row={i.id}
                  onClick={() => { setSel(i.id); setOpen(i.id) }}
                  className={clsx('flex w-full items-center gap-4 px-5 py-4 text-left transition-colors',
                    sel === i.id ? 'bg-accent-soft/60' : 'hover:bg-panel-2')}>
                  <ScoreRing score={i.confidence} size={30} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-fg-3 tabular-nums">#{i.id}</span>
                      <span className="truncate text-[13.5px] font-medium">{i.summary || '—'}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-fg-3">
                      <span>{groups.get(i.group_id)?.name}</span>
                      <span>·</span><span>{categoryLabel[i.category] ?? i.category}</span>
                      {i.model && <><span>·</span><span>{i.model}</span></>}
                      <Badge tone={rt} className="ml-1">{rl}</Badge>
                      {overdue && <Badge tone="bad">供应商未回复</Badge>}
                      {i.related_messages > 1 && <span>合并 {i.related_messages} 条</span>}
                    </div>
                  </div>
                  {i.assignee_id && users.get(i.assignee_id) && (
                    <span title={`负责人：${users.get(i.assignee_id)!.display_name}`}><Avatar name={users.get(i.assignee_id)!.display_name} size={24} /></span>
                  )}
                  <div className="hidden shrink-0 flex-col items-end gap-1 sm:flex">
                    <Badge tone={st} dot>{sl}</Badge>
                    <span className="text-[11px] text-fg-3">{ago(i.created_at, now)}</span>
                  </div>
                  <ChevronRight size={15} className="shrink-0 text-fg-3" />
                </motion.button>
              )
            })}
          </AnimatePresence>
        </Card>
      )}
      {rows.length > 0 && <p className="mt-3 text-xs text-fg-3"><Kbd>J</Kbd><Kbd>K</Kbd> 选择 · <Kbd>↵</Kbd> 打开 · 详情里 <Kbd>R</Kbd> 标记解决</p>}
      <IncidentDrawer i={data.find((x) => x.id === openId)} groups={groups} workflows={workflows} onClose={() => setOpen(null)} />
    </div>
  )
}
