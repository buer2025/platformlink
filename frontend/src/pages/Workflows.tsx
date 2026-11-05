import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ArrowRight, Bot, Database, Filter, Plus, Send, ShieldCheck, Workflow as WorkflowIcon } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api, categoryLabel, del, lookupLabel, post, put, type Lookup, type Workflow } from '../api'
import { useHotkeys } from '../hooks'
import { Badge, Button, Card, Drawer, Empty, Field, Input, listItem, PageHeader, Skeleton, Switch, TagInput, Textarea } from '../ui'

const blank: Omit<Workflow, 'id'> = {
  name: '', description: '', enabled: true, priority: 100, categories: [], keywords: [], group_tags: [],
  lookups: ['request_id', 'recent_errors', 'channel'], instructions: '', route_supplier: true, automation: '',
}
const CATS = ['error', 'rate_limit', 'usage_request', 'other']
const LOOKUPS = Object.keys(lookupLabel) as Lookup[]

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={clsx(
      'h-7 rounded-lg border px-2.5 text-xs font-medium transition-all duration-150 active:scale-95',
      on ? 'border-accent/40 bg-accent-soft text-accent' : 'border-line bg-panel text-fg-2 hover:border-line-strong')}>
      {children}
    </button>
  )
}

function Editor({ value, onClose }: { value: Partial<Workflow> | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState<Partial<Workflow>>(blank)
  const [prev, setPrev] = useState(value)
  if (value !== prev) { setPrev(value); if (value) setF({ ...blank, ...value }) }
  const set = <K extends keyof Workflow>(k: K, v: Workflow[K]) => setF((x) => ({ ...x, [k]: v }))
  const toggle = <T,>(list: T[], x: T) => (list.includes(x) ? list.filter((y) => y !== x) : [...list, x])
  const save = useMutation({
    mutationFn: () => {
      const { id, ...body } = f
      return id ? put<Workflow>(`/workflows/${id}`, body) : post<Workflow>('/workflows', body)
    },
    onSuccess: (w) => { toast.success(`链路「${w.name}」已保存`); onClose() },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['workflows'] }),
  })
  return (
    <Drawer open={!!value} onClose={onClose} width={640} title={f.id ? `编辑链路：${value?.name}` : '新建链路'}
      subtitle="定义一类问题：什么时候触发 → 查哪些数据 → Agent 怎么判断 → 怎么发出去。"
      footer={<>
        <Button variant="primary" disabled={!f.name?.trim() || save.isPending} onClick={() => save.mutate()} kbd="⌘↵">保存</Button>
        <Button variant="ghost" onClick={onClose}>取消</Button>
        <label className="ml-auto flex items-center gap-2 text-xs text-fg-2"><Switch checked={!!f.enabled} onChange={(v) => set('enabled', v)} label="启用" />启用</label>
      </>}>
      <form className="space-y-6" onSubmit={(e) => e.preventDefault()}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && f.name?.trim()) save.mutate() }}>
        <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
          <Field label="名称"><Input autoFocus value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="例如：模型用量需求" /></Field>
          <Field label="优先级" hint="小的先匹配"><Input inputMode="numeric" value={f.priority} onChange={(e) => set('priority', parseInt(e.target.value) || 0)} /></Field>
          <div className="sm:col-span-2"><Field label="说明"><Input value={f.description} onChange={(e) => set('description', e.target.value)} /></Field></div>
        </div>
        <Step n={1} icon={<Filter size={14} />} title="触发条件" desc="命中任意一个类别或关键词即走这条链路">
          <div className="flex flex-wrap gap-1.5">
            {CATS.map((c) => <Chip key={c} on={!!f.categories?.includes(c)} onClick={() => set('categories', toggle(f.categories ?? [], c))}>{categoryLabel[c]}</Chip>)}
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Field label="关键词"><TagInput value={f.keywords ?? []} onChange={(v) => set('keywords', v)} placeholder="扩容, 加量" /></Field>
            <Field label="只对带这些标识的群生效" hint="留空=全部"><TagInput tone="accent" value={f.group_tags ?? []} onChange={(v) => set('group_tags', v)} placeholder="VIP" /></Field>
          </div>
        </Step>
        <Step n={2} icon={<Database size={14} />} title="数据查询" desc="Agent 开始判断前先从日志库查好的数据；排查中它还能自己调用更多工具">
          <div className="flex flex-wrap gap-1.5">
            {LOOKUPS.map((l) => <Chip key={l} on={!!f.lookups?.includes(l)} onClick={() => set('lookups', toggle(f.lookups ?? [], l))}>{lookupLabel[l]}</Chip>)}
          </div>
        </Step>
        <Step n={3} icon={<Bot size={14} />} title="Agent 处理说明" desc="像给新同事交代工作一样写：怎么判断、怎么回复、注意什么">
          <Textarea rows={6} value={f.instructions} onChange={(e) => set('instructions', e.target.value)}
            placeholder={'1. 先说明客户当前量级…\n2. 回复客户时询问目标 RPM…'} />
        </Step>
        <Step n={4} icon={<Send size={14} />} title="发送策略" last>
          <div className="space-y-3">
            <label className="flex items-center justify-between gap-4 text-[13px]">
              <span>判定为供应商问题时，自动转交供应商群</span>
              <Switch checked={!!f.route_supplier} onChange={(v) => set('route_supplier', v)} label="转交供应商" />
            </label>
            <label className="flex items-center justify-between gap-4 text-[13px]">
              <span className="flex items-center gap-1.5"><ShieldCheck size={14} className="text-warn" />这条链路的消息一律人工确认后再发</span>
              <Switch checked={f.automation === 'confirm'} onChange={(v) => set('automation', v ? 'confirm' : '')} label="人工确认" />
            </label>
          </div>
        </Step>
      </form>
    </Drawer>
  )
}

function Step({ n, icon, title, desc, children, last }: { n: number; icon: ReactNode; title: string; desc?: string; children: ReactNode; last?: boolean }) {
  return (
    <section className="relative pl-9">
      {!last && <span className="absolute top-8 bottom-[-20px] left-[13px] w-px bg-line" />}
      <span className="absolute top-0 left-0 grid size-[27px] place-items-center rounded-full bg-accent-soft text-accent">{icon}</span>
      <h3 className="text-[13px] font-semibold"><span className="mr-1 text-fg-3">{n}.</span>{title}</h3>
      {desc && <p className="mt-0.5 mb-2.5 text-xs text-fg-3">{desc}</p>}
      <div className={desc ? '' : 'mt-2.5'}>{children}</div>
    </section>
  )
}

export default function Workflows() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<Partial<Workflow> | null>(null)
  const { data = [], isLoading } = useQuery({ queryKey: ['workflows'], queryFn: () => api<Workflow[]>('/workflows') })
  const toggle = useMutation({
    mutationFn: (w: Workflow) => (w.enabled ? del(`/workflows/${w.id}`) : put(`/workflows/${w.id}`, { ...w, enabled: true })),
    onSettled: () => qc.invalidateQueries({ queryKey: ['workflows'] }),
  })
  useHotkeys({ c: () => setEditing({ ...blank }) }, !editing)
  return (
    <div>
      <PageHeader title="处理链路" desc="不同类型的问题走不同的处理流程。客户消息按优先级匹配第一条命中的链路。">
        <Button variant="primary" icon={<Plus size={14} />} onClick={() => setEditing({ ...blank })} kbd="C">新建链路</Button>
      </PageHeader>
      {isLoading ? <Skeleton /> : data.length === 0 ? (
        <Empty icon={<WorkflowIcon size={18} />} title="还没有链路">没有链路时，所有问题按默认方式排查。</Empty>
      ) : (
        <div className="space-y-2.5">
          <AnimatePresence initial={false}>
            {data.map((w) => (
              <motion.div layout {...listItem} key={w.id}>
                <Card className={clsx('cursor-pointer p-4 transition-[border,opacity] hover:border-line-strong', !w.enabled && 'opacity-55')}
                  onClick={() => setEditing(w)}>
                  <div className="flex items-center gap-3">
                    <span className="grid size-8 place-items-center rounded-lg bg-accent-soft text-accent"><WorkflowIcon size={15} /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2"><h3 className="text-[14px] font-semibold">{w.name}</h3>
                        <span className="text-[11px] text-fg-3">优先级 {w.priority}</span>
                        {w.automation === 'confirm' && <Badge tone="warn">人工确认</Badge>}
                      </div>
                      {w.description && <p className="mt-0.5 truncate text-xs text-fg-3">{w.description}</p>}
                    </div>
                    <span onClick={(e) => e.stopPropagation()}><Switch checked={w.enabled} onChange={() => toggle.mutate(w)} label="启用" /></span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
                    {w.categories.map((c) => <Badge key={c} tone="info">{categoryLabel[c] ?? c}</Badge>)}
                    {w.keywords.slice(0, 4).map((k) => <Badge key={k}>{k}</Badge>)}
                    {w.group_tags.map((t) => <Badge key={t} tone="accent">#{t}</Badge>)}
                    <ArrowRight size={13} className="mx-0.5 text-fg-3" />
                    {w.lookups.map((l) => <Badge key={l} tone="violet">{lookupLabel[l]}</Badge>)}
                    <ArrowRight size={13} className="mx-0.5 text-fg-3" />
                    <Badge tone="ok">Agent 判断{w.route_supplier ? ' → 转交供应商' : ''}</Badge>
                  </div>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
      <Editor value={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
