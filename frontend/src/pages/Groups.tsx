import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Brain, ChevronRight, Database, MessagesSquare, Search, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { api, del, fmtTime, patch, platformLabel, post, type Group, type Message } from '../api'
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

type Profile = { summary: string; address: string; tone: string; particles: string[]; emoji: string[]; phrases: string[]; taboos: string[]; samples: string[] }

/** 风格标记：手动打标签，或从历史聊天里学习；右侧随时试一句看效果。 */
function StyleSection({ g, f, set, ev }: { g: Group; f: Record<string, any>; set: (k: string) => (v: unknown) => void; ev: (k: string) => (e: { target: { value: string } }) => void }) {
  const [learned, setLearned] = useState<{ profile: Profile; based_on: { ours: number } } | null>(null)
  const [sample, setSample] = useState('收到，这个问题是上游限流导致的，我们已经在协调扩容了，预计半小时内恢复。')
  const learn = useMutation({
    mutationFn: () => post<{ profile: Profile; based_on: { ours: number } }>(`/groups/${g.id}/style/learn`),
    onSuccess: setLearned,
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })
  const preview = useMutation({
    mutationFn: () => post<{ text: string }>(`/groups/${g.id}/style/preview`, {
      text: sample, style: {
        address: f.address, tone: f.tone, summary: f.summary, particles: f.particles, emoji: f.emoji,
        phrases: f.phrases, taboos: f.taboos, samples: splitLines(f.samples),
      },
    }),
  })
  const apply = () => {
    if (!learned) return
    const p = learned.profile
    set('summary')(p.summary); set('address')(p.address || f.address); set('tone')(p.tone || f.tone)
    set('particles')(p.particles); set('emoji')(p.emoji); set('phrases')(p.phrases); set('taboos')(p.taboos)
    set('samples')(p.samples.join('\n'))
    setLearned(null)
    toast.success('已填入学到的风格，保存后生效')
  }
  return (
    <section>
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h3 className="text-[13px] font-semibold">说话风格</h3>
          <p className="mt-0.5 text-xs text-fg-3">Agent 发到这个群的每条消息都会按这里改写，让对方觉得是熟悉的人在回。</p>
        </div>
        <Button size="sm" icon={<Sparkles size={13} />} disabled={learn.isPending} onClick={() => learn.mutate()}>
          {learn.isPending ? '正在分析聊天记录…' : '从聊天记录学习'}
        </Button>
      </div>
      <AnimatePresence>
        {learned && (
          <motion.div initial={{ opacity: 0, y: -6, height: 0 }} animate={{ opacity: 1, y: 0, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            className="mt-3 overflow-hidden rounded-xl bg-violet-soft/60 ring-1 ring-violet/20 ring-inset">
            <div className="p-4 text-[13px]">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-violet"><Sparkles size={12} />分析了我方 {learned.based_on.ours} 条发言</div>
              <p className="text-fg">{learned.profile.summary}</p>
              <div className="mt-2.5 flex flex-wrap gap-1">
                {learned.profile.address && <Badge tone="accent">称呼 · {learned.profile.address}</Badge>}
                {learned.profile.tone && <Badge>{learned.profile.tone}</Badge>}
                {learned.profile.particles.map((x) => <Badge key={x} tone="violet">{x}</Badge>)}
                {learned.profile.emoji.map((x) => <Badge key={x}>{x}</Badge>)}
                {learned.profile.phrases.map((x) => <Badge key={x}>「{x}」</Badge>)}
              </div>
              <div className="mt-3 flex gap-2">
                <Button size="sm" variant="primary" onClick={apply}>填入下方</Button>
                <Button size="sm" variant="ghost" onClick={() => setLearned(null)}>不用了</Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="mt-4 grid gap-3.5 sm:grid-cols-2">
        <Field label="称呼"><Input value={f.address} onChange={ev('address')} placeholder="王总 / 哥 / 老师" /></Field>
        <Field label="语气"><Input value={f.tone} onChange={ev('tone')} placeholder="正式 / 专业亲和 / 轻松 / 熟络 / 英文" /></Field>
        <Field label="语气词" hint="回车添加"><TagInput tone="violet" value={f.particles} onChange={set('particles')} placeholder="哈、呀、~" /></Field>
        <Field label="常用表情"><TagInput value={f.emoji} onChange={set('emoji')} placeholder="🙏 👌" /></Field>
        <Field label="口头禅"><TagInput value={f.phrases} onChange={set('phrases')} placeholder="辛苦啦、好嘞" /></Field>
        <Field label="禁忌"><TagInput tone="bad" value={f.taboos} onChange={set('taboos')} placeholder="不提竞品" /></Field>
        <Field label="整体风格" wide><Input value={f.summary} onChange={ev('summary')} placeholder="例如：熟络但专业，称呼王总，句子短，偶尔用~" /></Field>
        <Field label="真实发言样本" hint="每行一条，Agent 会模仿句式和长度" wide><Textarea rows={3} value={f.samples} onChange={ev('samples')} /></Field>
      </div>
      <div className="mt-4 rounded-xl bg-panel-2/70 p-4 ring-1 ring-line ring-inset">
        <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-fg-2"><Wand2 size={13} />试一句</div>
        <div className="flex gap-2">
          <Input value={sample} onChange={(e) => setSample(e.target.value)} />
          <Button size="sm" className="h-8" disabled={!sample.trim() || preview.isPending} onClick={() => preview.mutate()}>
            {preview.isPending ? '改写中…' : '看效果'}
          </Button>
        </div>
        <AnimatePresence mode="wait">
          {preview.data && (
            <motion.div key={preview.data.text} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
              className="mt-3 max-w-[90%] rounded-2xl rounded-tl-md bg-panel px-4 py-2.5 text-[14px] leading-relaxed shadow-card">
              {preview.data.text}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  )
}

type Memory = { id: number; text: string; incident_id: number | null; created_at: string }

/** 客户记忆：每次会话关闭自动沉淀一句，新会话时 Agent 会先看这些。 */
function Memories({ groupId }: { groupId: number }) {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['memories', groupId], queryFn: () => api<Memory[]>(`/groups/${groupId}/memories`) })
  const remove = useMutation({
    mutationFn: (id: number) => del(`/memories/${id}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['memories', groupId] }),
  })
  return (
    <section>
      <h3 className="flex items-center gap-1.5 text-[13px] font-semibold"><Brain size={14} />客户记忆</h3>
      <p className="mt-0.5 text-xs text-fg-3">每次会话结束（手动解决或 24 小时无动静自动关闭）沉淀一句。新问题进来时 Agent 会先看最近 5 条。</p>
      {data.length === 0 ? <p className="mt-3 text-xs text-fg-3">还没有记忆</p> : (
        <ul className="mt-3 space-y-1">
          <AnimatePresence initial={false}>
            {data.map((m) => (
              <motion.li key={m.id} layout exit={{ opacity: 0, x: -12 }}
                className="group flex items-start gap-3 rounded-lg px-2 py-1.5 text-[13px] hover:bg-panel-2">
                <span className="tnum mt-0.5 w-11 shrink-0 text-[11px] text-fg-3">{new Date(m.created_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</span>
                <span className="flex-1 leading-relaxed">{m.text}</span>
                <button aria-label="删除这条记忆" className="mt-0.5 text-fg-3 opacity-0 transition-opacity group-hover:opacity-100 hover:text-bad"
                  onClick={() => remove.mutate(m.id)}><Trash2 size={13} /></button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
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
      tags: g.tags, api_users: g.api_users, bot_webhook: g.bot_webhook ?? '', channel_ids: g.channel_ids.map(String),
      owner: g.owner, backup_owner: g.backup_owner, extra_delay_seconds: String(g.extra_delay_seconds), sla_minutes: String(g.sla_minutes ?? 0),
      address: str(st.address), tone: str(st.tone), summary: str(st.summary), phrases: list(st.phrases), taboos: list(st.taboos),
      particles: list(st.particles), emoji: list(st.emoji), samples: lines(st.samples),
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
      put('address', f.address.trim()); put('tone', f.tone.trim()); put('summary', f.summary.trim()); put('phrases', f.phrases); put('taboos', f.taboos)
      put('particles', f.particles); put('emoji', f.emoji)
      put('samples', splitLines(f.samples))
      return patch<Group>(`/groups/${g!.id}`, {
        name: f.name.trim() || g!.name, kind: f.kind, automation: f.automation, level: f.level.trim(),
        models: f.models, contacts: f.contacts, tags: f.tags, api_users: f.api_users, bot_webhook: f.bot_webhook.trim(),
        channel_ids: (f.channel_ids as string[]).map(Number).filter((n) => Number.isInteger(n) && n > 0),
        owner: f.owner.trim(), backup_owner: f.backup_owner.trim(),
        extra_delay_seconds: Math.max(0, parseInt(f.extra_delay_seconds) || 0),
        sla_minutes: Math.max(0, parseInt(f.sla_minutes) || 0), style,
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
              {Object.entries(kindLabel).map(([k, v]) => <option key={k} value={k}>{k === 'internal' ? '内部群（升级人工时在这里 @ 负责人）' : v}</option>)}</Select></Field>
            <Field label="自动化" hint="只观察：Agent 不发言"><Select value={f.automation} onChange={ev('automation')}>
              {Object.entries(autoLabel).map(([k, [v]]) => <option key={k} value={k}>{v}</option>)}</Select></Field>
            <Field label="额外倒计时（秒）" hint="VIP 群可加长"><Input inputMode="numeric" value={f.extra_delay_seconds} onChange={ev('extra_delay_seconds')} /></Field>
            <Field label="等待提醒（分钟）" hint="客户等这么久没人回就提醒负责人，0 = 默认 15"><Input inputMode="numeric" value={f.sla_minutes} onChange={ev('sla_minutes')} /></Field>
          </Section>
          {g!.platform === 'wecom' && (
            <Section title="企业微信发送" desc="企业微信用「群机器人」发消息：群设置 → 添加群机器人 → 复制 Webhook 地址。群机器人只能加到内部群；外部客户群企业微信不允许机器人发消息，请把自动化设为「需人工确认」，由同事复制 AI 草稿发送。">
              <Field label="群机器人 Webhook" wide><Input value={f.bot_webhook} onChange={ev('bot_webhook')} placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…" /></Field>
            </Section>
          )}
          <Section title="业务标记" desc="标识可被处理链路用来限定范围，也会告诉 Agent 这个群的背景。">
            <Field label="标识" hint="回车添加"><TagInput tone="accent" value={f.tags} onChange={set('tags')} placeholder="VIP, 视频, 海外" /></Field>
            <Field label="等级"><Input value={f.level} onChange={ev('level')} placeholder="VIP / KA / 主力供应商" /></Field>
            <Field label="涉及模型" hint={f.kind === 'supplier' ? '供应商群按此路由' : ''}><TagInput tone="violet" value={f.models} onChange={set('models')} placeholder="seedance" /></Field>
            <Field label="对接人" hint="转交时 @ 的账号"><TagInput value={f.contacts} onChange={set('contacts')} /></Field>
            <Field label="负责人" hint="Agent 处理不了时 @ 他"><Input value={f.owner} onChange={ev('owner')} placeholder="我方人员姓名或平台账号" /></Field>
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
          <StyleSection g={g!} f={f} set={set} ev={ev} />
          <Memories groupId={g!.id} />
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
