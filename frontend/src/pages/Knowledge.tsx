import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Flame, Plus, Search } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { ago, api, post, put, respLabel, type Knowledge } from '../api'
import { useHotkeys } from '../hooks'
import { Badge, Button, Card, Drawer, Empty, Field, Input, Kbd, listItem, PageHeader, Select, Skeleton, Switch, TagInput, Textarea } from '../ui'

const blank: Partial<Knowledge> = {
  title: '', symptoms: '', keywords: [], models: [], responsible: '', root_cause: '', solution: '', reply_template: '', enabled: true,
}

/** 新建 / 编辑知识；value 为 null 时关闭。 */
export function KnowledgeEditor({ value, onClose }: { value: Partial<Knowledge> | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [f, setF] = useState<Partial<Knowledge>>(blank)
  const [prev, setPrev] = useState(value)
  if (value !== prev) { setPrev(value); if (value) setF({ ...blank, ...value }) }
  const set = <K extends keyof Knowledge>(k: K, v: Knowledge[K]) => setF((x) => ({ ...x, [k]: v }))
  const save = useMutation({
    mutationFn: () => {
      const { id, hits: _h, created_at: _c, updated_at: _u, ...body } = f
      return id ? put<Knowledge>(`/knowledge/${id}`, body) : post<Knowledge>('/knowledge', body)
    },
    onSuccess: (k) => { toast.success(`已保存知识 #${k.id}`, { description: '之后 Agent 遇到相似问题会自动参考' }); onClose() },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
    onSettled: () => { qc.invalidateQueries({ queryKey: ['knowledge'] }); qc.invalidateQueries({ queryKey: ['events'] }) },
  })
  return (
    <Drawer open={!!value} onClose={onClose} width={600}
      title={f.id ? `编辑知识 #${f.id}` : f.source_incident_id ? `从事件 #${f.source_incident_id} 沉淀知识` : '新建知识'}
      subtitle="写清现象和处理方式，Agent 排查时会按模型、关键词和现象自动检索。"
      footer={<>
        <Button variant="primary" disabled={!f.title?.trim() || save.isPending} onClick={() => save.mutate()} kbd="⌘↵">保存</Button>
        <Button variant="ghost" onClick={onClose}>取消</Button>
        <label className="ml-auto flex items-center gap-2 text-xs text-fg-2"><Switch checked={!!f.enabled} onChange={(v) => set('enabled', v)} label="启用" />启用</label>
      </>}>
      <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => e.preventDefault()}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && f.title?.trim()) save.mutate() }}>
        <Field label="标题" wide><Input autoFocus value={f.title} onChange={(e) => set('title', e.target.value)} placeholder="例如：seedance 上游 429 需找供应商扩量" /></Field>
        <Field label="现象" hint="报错原文、错误码、客户描述" wide>
          <Textarea rows={3} value={f.symptoms} onChange={(e) => set('symptoms', e.target.value)} placeholder="upstream error: status_code=429, Too Many Requests" />
        </Field>
        <Field label="关键词" hint="回车添加"><TagInput value={f.keywords ?? []} onChange={(v) => set('keywords', v)} placeholder="429, 限流" /></Field>
        <Field label="适用模型" hint="前缀匹配"><TagInput tone="violet" value={f.models ?? []} onChange={(v) => set('models', v)} placeholder="seedance" /></Field>
        <Field label="责任方">
          <Select value={f.responsible} onChange={(e) => set('responsible', e.target.value)}>
            <option value="">—</option>
            {Object.entries(respLabel).map(([k, [v]]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <div />
        <Field label="根因" wide><Textarea rows={2} value={f.root_cause} onChange={(e) => set('root_cause', e.target.value)} /></Field>
        <Field label="处理步骤" hint="下次照着做" wide><Textarea rows={3} value={f.solution} onChange={(e) => set('solution', e.target.value)} /></Field>
        <Field label="客户回复参考" wide><Textarea rows={2} value={f.reply_template} onChange={(e) => set('reply_template', e.target.value)} /></Field>
      </form>
    </Drawer>
  )
}

export default function KnowledgePage() {
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<Partial<Knowledge> | null>(null)
  const { data = [], isLoading } = useQuery({
    queryKey: ['knowledge', q], queryFn: () => api<Knowledge[]>(`/knowledge${q.trim() ? `?q=${encodeURIComponent(q)}` : ''}`),
    placeholderData: (p) => p,
  })
  useHotkeys({ c: () => setEditing({ ...blank }), '/': () => document.getElementById('kb-search')?.focus() }, !editing)
  return (
    <div>
      <PageHeader title="知识库" desc="处理过的问题沉淀成经验。Agent 排查时自动检索，越用越准。">
        <Button variant="primary" icon={<Plus size={14} />} onClick={() => setEditing({ ...blank })} kbd="C">新建</Button>
      </PageHeader>
      <div className="relative mb-4">
        <Search size={15} className="absolute top-1/2 left-3 -translate-y-1/2 text-fg-3" />
        <Input id="kb-search" className="h-10 pl-9 text-sm" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="粘贴报错原文试试，看 Agent 会匹配到哪些经验…" />
        <Kbd className="absolute top-1/2 right-3 -translate-y-1/2">/</Kbd>
      </div>
      {isLoading ? <Skeleton /> : data.length === 0 ? (
        <Empty icon={<BookOpen size={18} />} title={q ? '没有匹配的经验' : '知识库还是空的'}>
          {q ? '换个关键词，或者新建一条。' : '在事件详情里点「沉淀为知识」，Agent 会根据处理过程自动起草。'}
        </Empty>
      ) : (
        <div className="grid gap-2.5 sm:grid-cols-2">
          <AnimatePresence initial={false} mode="popLayout">
            {data.map((k) => {
              const [rl, rt] = respLabel[k.responsible] ?? ['', 'slate']
              return (
                <motion.div layout {...listItem} key={k.id}>
                  <Card className="group h-full cursor-pointer p-4 transition-[border,transform] duration-150 hover:-translate-y-0.5 hover:border-line-strong"
                    onClick={() => setEditing(k)}>
                    <div className="flex items-start gap-2">
                      <h3 className="flex-1 text-[13.5px] leading-snug font-medium">{k.title}</h3>
                      {!k.enabled && <Badge>已停用</Badge>}
                      {k.hits > 0 && <span className="flex items-center gap-0.5 text-[11px] text-warn tabular-nums" title="被 Agent 引用次数"><Flame size={12} />{k.hits}</span>}
                    </div>
                    {k.symptoms && <p className="mt-1.5 line-clamp-2 font-mono text-[11.5px] text-fg-3">{k.symptoms}</p>}
                    {k.solution && <p className="mt-2 line-clamp-2 text-[13px] text-fg-2">{k.solution}</p>}
                    <div className="mt-3 flex flex-wrap items-center gap-1">
                      {rl && <Badge tone={rt}>{rl}</Badge>}
                      {k.models.map((m) => <Badge key={m} tone="violet">{m}</Badge>)}
                      {k.keywords.slice(0, 4).map((w) => <Badge key={w}>{w}</Badge>)}
                      <span className="ml-auto text-[11px] text-fg-3">
                        {k.source_incident_id && <Link onClick={(e) => e.stopPropagation()} className="mr-2 hover:text-accent" to={`/incidents?open=${k.source_incident_id}`}>来自 #{k.source_incident_id}</Link>}
                        {ago(k.updated_at)}
                      </span>
                    </div>
                  </Card>
                </motion.div>
              )
            })}
          </AnimatePresence>
        </div>
      )}
      <KnowledgeEditor value={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
