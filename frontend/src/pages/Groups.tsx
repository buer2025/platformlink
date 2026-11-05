import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { ChevronRight, Database, MessagesSquare, Search } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { api, fmtTime, patch, platformLabel, post, type Group, type Message } from '../api'
import { useHotkeys, useListNav } from '../hooks'
import { Badge, Button, Card, Drawer, Empty, Field, Input, PageHeader, Segmented, Select, Skeleton, TagInput, Textarea, type Tone } from '../ui'

const kindLabel = { customer: '客户群', supplier: '供应商群', internal: '内部群' }
const autoLabel: Record<Group['automation'], [string, Tone]> = { auto: ['按准确率自动', 'ok'], confirm: ['需人工确认', 'warn'], observe: ['只观察', 'slate'] }
const lines = (v: unknown) => (Array.isArray(v) ? v.join('\n') : '')
const str = (v: unknown) => (typeof v === 'string' ? v : '')
const splitLines = (s: string) => s.split(/\n+/).map((x) => x.trim()).filter(Boolean)
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : [])

function Section({ title, desc, children, icon }: { title: string; desc?: string; children: ReactNode; icon?: ReactNode }) {
  return (
    <section>
      <h3 className="flex items-center gap-1.5 text-[13px] font-semibold">{icon}{title}</h3>
      {desc && <p className="mt-0.5 text-xs text-fg-3">{desc}</p>}
      <div className="mt-3 grid gap-3.5 sm:grid-cols-2">{children}</div>
    </section>
  )
}

function Editor({ g, onClose }: { g: Group | undefined; onClose: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState<Record<string, any>>({})
  const [prev, setPrev] = useState<Group | undefined>(undefined)
  if (g && g !== prev) {
    setPrev(g)
    const st = g.style ?? {}
    setF({
      name: g.name, kind: g.kind, automation: g.automation, level: g.level, models: g.models, contacts: g.contacts,
      tags: g.tags, api_users: g.api_users, channel_ids: g.channel_ids.map(String),
      owner: g.owner, backup_owner: g.backup_owner, extra_delay_seconds: String(g.extra_delay_seconds),
      address: str(st.address), tone: str(st.tone), phrases: list(st.phrases), taboos: list(st.taboos), samples: lines(st.samples),
    })
  }
  const set = (k: string) => (v: unknown) => setF((x) => ({ ...x, [k]: v }))
  const ev = (k: string) => (e: { target: { value: string } }) => set(k)(e.target.value)
  const { data: msgs = [] } = useQuery({ queryKey: ['group-msgs', g?.id], queryFn: () => api<Message[]>(`/groups/${g!.id}/messages`), enabled: !!g })
  const test = useMutation({
    mutationFn: () => post<{ configured: boolean; found: boolean; text: string }>('/lookup/test', {
      api_users: f.api_users, lookups: ['recent_errors', 'usage'], model: '',
    }),
  })
  const save = useMutation({
    mutationFn: () => {
      const style: Record<string, unknown> = { ...(g!.style ?? {}) }
      const put = (k: string, v: unknown) => { if ((Array.isArray(v) && v.length) || (typeof v === 'string' && v)) style[k] = v; else delete style[k] }
      put('address', f.address.trim()); put('tone', f.tone.trim()); put('phrases', f.phrases); put('taboos', f.taboos)
      put('samples', splitLines(f.samples))
      return patch<Group>(`/groups/${g!.id}`, {
        name: f.name.trim() || g!.name, kind: f.kind, automation: f.automation, level: f.level.trim(),
        models: f.models, contacts: f.contacts, tags: f.tags, api_users: f.api_users,
        channel_ids: (f.channel_ids as string[]).map(Number).filter((n) => Number.isInteger(n) && n > 0),
        owner: f.owner.trim(), backup_owner: f.backup_owner.trim(),
        extra_delay_seconds: Math.max(0, parseInt(f.extra_delay_seconds) || 0), style,
      })
    },
    onSuccess: (r) => { toast.success(`已保存「${r.name}」`); onClose() },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    onSettled: () => qc.invalidateQueries({ queryKey: ['groups'] }),
  })
  const ready = g && f.name !== undefined
  return (
    <Drawer open={!!g} onClose={onClose} width={660} title={g?.name}
      subtitle={g && <span className="flex items-center gap-1.5"><Badge>{platformLabel[g.platform]}</Badge><span className="font-mono">{g.external_id}</span></span>}
      footer={<><Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()} kbd="⌘↵">保存</Button><Button variant="ghost" onClick={onClose}>取消</Button></>}>
      {ready && (
        <form className="space-y-7" onSubmit={(e) => e.preventDefault()}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save.mutate() }}>
          <Section title="基本">
            <Field label="群名称"><Input value={f.name} onChange={ev('name')} /></Field>
            <Field label="类型"><Select value={f.kind} onChange={ev('kind')}>
              {Object.entries(kindLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="自动化" hint="只观察：Agent 不发言"><Select value={f.automation} onChange={ev('automation')}>
              {Object.entries(autoLabel).map(([k, [v]]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="额外倒计时（秒）" hint="VIP 群可加长"><Input inputMode="numeric" value={f.extra_delay_seconds} onChange={ev('extra_delay_seconds')} /></Field>
          </Section>
          <Section title="业务标记" desc="标识可被处理链路用来限定范围，也会告诉 Agent 这个群的背景。">
            <Field label="标识" hint="回车添加"><TagInput tone="accent" value={f.tags} onChange={set('tags')} placeholder="VIP, 视频, 海外" /></Field>
            <Field label="等级"><Input value={f.level} onChange={ev('level')} placeholder="VIP / KA / 主力供应商" /></Field>
            <Field label="涉及模型" hint={f.kind === 'supplier' ? '供应商群按此路由' : ''}><TagInput tone="violet" value={f.models} onChange={set('models')} placeholder="seedance" /></Field>
            <Field label="对接人" hint="转交时 @ 的账号"><TagInput value={f.contacts} onChange={set('contacts')} /></Field>
            <Field label="负责人"><Input value={f.owner} onChange={ev('owner')} placeholder="负责人" /></Field>
            <Field label="备份负责人"><Input value={f.backup_owner} onChange={ev('backup_owner')} placeholder="备份" /></Field>
          </Section>
          <Section icon={<Database size={14} />} title="日志库绑定" desc={f.kind === 'supplier'
            ? '填该供应商在 new-api 中对应的渠道 ID。日志定位到这些渠道出错时，会精确转交到本群。'
            : '填该客户在 new-api 中的用户名或用户 ID，Agent 才能查他的近期错误和用量。'}>
            {f.kind === 'supplier'
              ? <Field label="渠道 ID" wide><TagInput tone="info" value={f.channel_ids} onChange={set('channel_ids')} placeholder="7, 12" /></Field>
              : <>
                <Field label="new-api 用户" wide><TagInput tone="info" value={f.api_users} onChange={set('api_users')} placeholder="用户名或用户 ID" /></Field>
                <div className="sm:col-span-2">
                  <Button size="sm" disabled={!f.api_users.length || test.isPending} onClick={() => test.mutate()}>{test.isPending ? '查询中…' : '测试查询'}</Button>
                  {test.data && <pre className={clsx('mt-2 rounded-lg border border-line bg-panel-2 p-2.5 font-mono text-[11.5px] whitespace-pre-wrap', test.data.found ? 'text-fg-2' : 'text-fg-3')}>{test.data.text}</pre>}
                </div>
              </>}
          </Section>
          <Section title="风格标记" desc="Agent 回复时据此调整语气，让消息不像机器发的。">
            <Field label="称呼"><Input value={f.address} onChange={ev('address')} placeholder="王总 / 哥 / 老师" /></Field>
            <Field label="语气"><Input value={f.tone} onChange={ev('tone')} placeholder="正式 / 轻松 / 熟络 / 英文" /></Field>
            <Field label="口头禅"><TagInput value={f.phrases} onChange={set('phrases')} placeholder="辛苦啦" /></Field>
            <Field label="禁忌"><TagInput tone="bad" value={f.taboos} onChange={set('taboos')} placeholder="不提竞品" /></Field>
            <Field label="风格样本" hint="每行一条我方在该群的典型发言" wide><Textarea rows={3} value={f.samples} onChange={ev('samples')} /></Field>
          </Section>
          <section>
            <h3 className="mb-2 text-[13px] font-semibold">最近消息</h3>
            {msgs.length === 0 ? <p className="text-xs text-fg-3">暂无</p> : (
              <ul className="max-h-72 space-y-1.5 overflow-y-auto rounded-lg border border-line p-2.5 text-[13px]">
                {msgs.slice(0, 50).map((m) => (
                  <li key={m.id} className={m.from_us ? 'text-accent' : ''}>
                    <span className="text-[11px] text-fg-3">{fmtTime(m.created_at).slice(5)} · {m.sender}</span> {m.text}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </form>
      )}
    </Drawer>
  )
}

export default function Groups() {
  const { data = [], isLoading } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState('')
  const [kind, setKind] = useState<'' | Group['kind']>('')
  const [platform, setPlatform] = useState('')
  const rows = data.filter((g) =>
    (!kind || g.kind === kind) && (!platform || g.platform === platform) &&
    (!q || [g.name, g.owner, g.level, ...g.models, ...g.tags].some((x) => x.toLowerCase().includes(q.toLowerCase()))))
  const openId = Number(params.get('open')) || null
  const setOpen = (id: number | null) => setParams(id ? { open: String(id) } : {}, { replace: true })
  const { sel, setSel, current, move } = useListNav(rows)
  useHotkeys({ j: () => move(1), k: () => move(-1), enter: () => current && setOpen(current.id), '/': () => document.getElementById('group-search')?.focus() }, !openId)
  const observing = data.filter((g) => g.automation === 'observe').length

  return (
    <div>
      <PageHeader title="群与标记" desc={observing ? `${observing} 个新群还在「只观察」，打好标记后 Agent 才会处理。` : '机器人加入的群会自动出现在这里。'}>
        <Segmented value={kind} onChange={setKind} options={[
          { value: '', label: '全部' }, { value: 'customer', label: '客户' }, { value: 'supplier', label: '供应商' }, { value: 'internal', label: '内部' },
        ]} />
      </PageHeader>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1 sm:max-w-xs">
          <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-fg-3" />
          <Input id="group-search" className="pl-8" placeholder="搜索群名 / 模型 / 负责人" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select className="w-auto" value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">全部平台</option>
          {Object.entries(platformLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <span className="text-xs text-fg-3">{rows.length} / {data.length} 个群</span>
      </div>
      {isLoading ? <Skeleton /> : data.length === 0 ? (
        <Empty icon={<MessagesSquare size={18} />} title="还没有群">把机器人拉进群并有人发言后，群会自动出现在这里（默认只观察）。</Empty>
      ) : (
        <Card className="divide-y divide-line overflow-hidden">
          {rows.map((g) => {
            const [al, at] = autoLabel[g.automation]
            return (
              <button key={g.id} data-row={g.id} onClick={() => { setSel(g.id); setOpen(g.id) }}
                className={clsx('flex w-full items-center gap-3 px-4 py-3 text-left transition-colors', sel === g.id ? 'bg-accent-soft/60' : 'hover:bg-panel-2')}>
                <span className={clsx('grid size-8 shrink-0 place-items-center rounded-lg text-[13px] font-semibold',
                  g.kind === 'supplier' ? 'bg-warn-soft text-warn' : g.kind === 'internal' ? 'bg-panel-2 text-fg-3' : 'bg-info-soft text-info')}>
                  {g.name.slice(0, 1)}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2"><span className="truncate text-[13.5px] font-medium">{g.name}</span>
                    <span className="text-[11px] text-fg-3">{platformLabel[g.platform]} · {kindLabel[g.kind]}</span></div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Badge tone={at} dot>{al}</Badge>
                    {g.level && <Badge tone="bad">{g.level}</Badge>}
                    {g.tags.map((t) => <Badge key={t} tone="accent">#{t}</Badge>)}
                    {g.models.map((m) => <Badge key={m} tone="violet">{m}</Badge>)}
                    {(g.api_users.length > 0 || g.channel_ids.length > 0) && <Badge tone="info"><Database size={10} />已绑定日志</Badge>}
                  </div>
                </div>
                {g.owner && <span className="hidden text-xs text-fg-3 sm:block">负责人 {g.owner}</span>}
                <ChevronRight size={15} className="shrink-0 text-fg-3" />
              </button>
            )
          })}
        </Card>
      )}
      <Editor g={data.find((g) => g.id === openId)} onClose={() => setOpen(null)} />
    </div>
  )
}
