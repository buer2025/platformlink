import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { ChevronRight, CircleAlert, Loader2 } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { api, callKindLabel, clock, fmtTime, type ModelCall, type ModelCallDetail } from '../api'
import { Badge } from '../ui'

const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`)
const fmtTok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

type ContentBlock = { type?: string; text?: string; thinking?: string; name?: string; input?: unknown; content?: unknown; is_error?: boolean }

/** Claude 的 content 块转成人能读的文字：文本原样、工具调用写成 name(args)、思考单独标出、签名等噪音去掉。 */
function readable(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return JSON.stringify(content, null, 2)
  return (content as ContentBlock[]).map((b) => {
    switch (b.type) {
      case 'text': return b.text ?? ''
      case 'thinking': return b.thinking ? `💭 ${b.thinking}` : ''
      case 'redacted_thinking': return ''
      case 'tool_use': return `→ 调用 ${b.name}(${JSON.stringify(b.input)})`
      case 'tool_result': return `${b.is_error ? '✗' : '←'} 工具结果\n${readable(b.content)}`
      case 'image': return '[图片]'
      default: return JSON.stringify(b, null, 2)
    }
  }).filter(Boolean).join('\n\n')
}

function outputText(raw: string) {
  try {
    const v = JSON.parse(raw)
    return Array.isArray(v) ? readable(v) : JSON.stringify(v, null, 2)
  } catch { return raw }
}

/** 把请求里的 system / messages 拆开展示；解析不了就原样显示。 */
function RequestView({ raw }: { raw: string }) {
  let req: Record<string, unknown> | null = null
  try { req = JSON.parse(raw) } catch { /* 原样展示 */ }
  if (!req) return <Pre text={raw} />
  const { system, messages, state, questions, tools, ...rest } = req
  const sys = Array.isArray(system) ? system.map((b: { text?: string }) => b.text ?? '').join('\n') : (system as string | undefined)
  return (
    <div className="space-y-2">
      {sys && <Block title="System">{sys}</Block>}
      {state !== undefined && <Block title="State">{typeof state === 'string' ? state : JSON.stringify(state, null, 2)}</Block>}
      {questions !== undefined && <Block title="Questions">{JSON.stringify(questions, null, 2)}</Block>}
      {Array.isArray(messages) && messages.map((m: { role: string; content: unknown }, i: number) => (
        <Block key={i} title={m.role === 'user' ? '输入' : '模型'}>
          {readable(m.content)}
        </Block>
      ))}
      {Array.isArray(tools) && <Block title="工具" dim>{tools.map((t: { name: string }) => t.name).join('、')}</Block>}
      <Block title="参数" dim>{JSON.stringify(rest)}</Block>
    </div>
  )
}

function Block({ title, children, dim }: { title: string; children: string; dim?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-[10.5px] font-medium tracking-wide text-fg-3 uppercase">{title}</div>
      <Pre text={children} dim={dim} />
    </div>
  )
}

function Pre({ text, dim }: { text: string; dim?: boolean }) {
  return (
    <pre className={clsx('max-h-80 overflow-auto rounded-lg border border-line bg-panel p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap',
      dim ? 'text-fg-3' : 'text-fg-2')}>{text}</pre>
  )
}

function CallDetail({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ['model-call', id], queryFn: () => api<ModelCallDetail>(`/model-calls/${id}`) })
  if (!data) return <div className="py-3 text-xs text-fg-3">加载中…</div>
  return (
    <div className="grid gap-3 pt-2 pb-1 lg:grid-cols-2">
      <div className="min-w-0"><RequestView raw={data.request} /></div>
      <div className="min-w-0">
        {data.error && <div className="mb-2 rounded-lg bg-bad-soft px-2.5 py-1.5 text-xs text-bad">{data.error}</div>}
        <Block title="输出">{data.status === 'running' ? '（还在进行中）' : outputText(data.response)}</Block>
      </div>
    </div>
  )
}

export function CallRow({ c, extra }: { c: ModelCall; extra?: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-line last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-1 py-2.5 text-left text-[13px] hover:bg-panel-2/50">
        <ChevronRight size={14} className={clsx('shrink-0 text-fg-3 transition-transform duration-200', open && 'rotate-90')} />
        <Badge tone={c.provider === 'jev' ? 'violet' : 'accent'}>{c.provider === 'jev' ? 'Jev' : 'Claude'}</Badge>
        <span className="min-w-0 truncate font-medium">{callKindLabel[c.kind] ?? c.kind}{c.step ? ` · 第 ${c.step} 轮` : ''}</span>
        {extra}
        <span className="ml-auto flex shrink-0 items-center gap-3 text-xs text-fg-3 tabular-nums">
          {c.status === 'running' ? <span className="inline-flex items-center gap-1 text-violet"><Loader2 size={12} className="animate-spin" />进行中</span>
            : c.status === 'error' ? <span className="inline-flex items-center gap-1 text-bad"><CircleAlert size={12} />失败</span>
              : <span>{fmtMs(c.latency_ms)}</span>}
          <span className="hidden w-20 text-right sm:inline" title="输入 / 输出 token">{fmtTok(c.input_tokens)} / {fmtTok(c.output_tokens)}</span>
          <span className="w-16 text-right" title={fmtTime(c.created_at)}>{clock(c.created_at, true)}</span>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden px-1">
            <div className="mb-1 text-[11px] text-fg-3">{c.model} · {fmtTime(c.created_at)}</div>
            <CallDetail id={c.id} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

/** 某个事件下的全部模型调用（包含建事件之前的分诊）。 */
export function IncidentCalls({ incidentId }: { incidentId: number }) {
  const { data = [] } = useQuery({
    queryKey: ['incident-calls', incidentId],
    queryFn: () => api<ModelCall[]>(`/incidents/${incidentId}/model-calls`),
  })
  if (!data.length) return <p className="py-6 text-center text-[13px] text-fg-3">还没有模型调用</p>
  const total = data.reduce((a, c) => a + c.input_tokens + c.output_tokens, 0)
  const ms = data.reduce((a, c) => a + c.latency_ms, 0)
  return (
    <div>
      <div className="mb-1 flex gap-4 text-xs text-fg-3 tabular-nums">
        <span>{data.length} 次调用</span><span>累计耗时 {fmtMs(ms)}</span><span>{fmtTok(total)} tokens</span>
      </div>
      <ul>{data.map((c) => <CallRow key={c.id} c={c} />)}</ul>
    </div>
  )
}

export function useCallCount(incidentId: number | undefined) {
  const { data = [] } = useQuery({
    queryKey: ['incident-calls', incidentId],
    queryFn: () => api<ModelCall[]>(`/incidents/${incidentId}/model-calls`),
    enabled: !!incidentId,
  })
  return data.length
}
