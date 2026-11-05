export type Platform = 'telegram' | 'feishu' | 'wecom'

export type Group = {
  id: number
  platform: Platform
  external_id: string
  name: string
  kind: 'customer' | 'supplier' | 'internal'
  models: string[]
  level: string
  contacts: string[]
  style: Record<string, unknown>
  automation: 'auto' | 'confirm' | 'observe'
  extra_delay_seconds: number
  sla_minutes: number
  owner: string
  backup_owner: string
  tags: string[]
  api_users: string[]
  channel_ids: number[]
  bot_webhook: string
}

export type Outbound = {
  id: number
  group_id: number
  group_name: string
  incident_id: number | null
  purpose: string
  text: string
  mentions: string[]
  score: number
  status: 'pending' | 'needs_approval' | 'sending' | 'sent' | 'cancelled' | 'failed'
  send_at: string | null
  error: string
  created_at: string
}

export type Incident = {
  id: number
  group_id: number
  category: string
  model: string
  request_id: string
  summary: string
  responsible: string
  evidence: string
  confidence: number
  status: 'open' | 'investigating' | 'waiting_supplier' | 'resolved'
  supplier_group_id: number | null
  ticket_sent_at: string | null
  supplier_replied_at: string | null
  supplier_last_reply: string
  reminders: number
  related_messages: number
  workflow_id: number | null
  lookup_result: string
  evidence_checks: { key: string; label: string; state: 'ok' | 'warn' | 'missing'; detail: string }[]
  knowledge_ids: number[]
  resolved_at: string | null
  escalated_at: string | null
  escalation_reason: string
  human_until: string | null
  human_by: string
  assignee_id: number | null
  created_at: string
}

export type IncidentEvent = { id: number; kind: string; text: string; actor: string; created_at: string }

export type Lookup = 'request_id' | 'recent_errors' | 'usage' | 'channel'

export type Workflow = {
  id: number
  name: string
  description: string
  enabled: boolean
  priority: number
  categories: string[]
  keywords: string[]
  group_tags: string[]
  lookups: Lookup[]
  instructions: string
  route_supplier: boolean
  automation: '' | 'confirm'
}

export type Knowledge = {
  id: number
  title: string
  symptoms: string
  keywords: string[]
  models: string[]
  responsible: string
  root_cause: string
  solution: string
  reply_template: string
  source_incident_id: number | null
  enabled: boolean
  hits: number
  created_at: string
  updated_at: string
}

export type Message = {
  id: number
  group_id: number
  sender: string
  text: string
  from_us: boolean
  incident_id: number | null
  created_at: string
}

export type User = { id: number; username: string; display_name: string; role: 'admin' | 'agent'; active: boolean; on_duty: boolean }
export type Me = { id: number | null; name: string; role: 'admin' | 'agent'; on_duty: boolean; builtin: boolean }

export type Staff = { id: number; name: string; platform: Platform; account: string }

const TOKEN_KEY = 'platformlink.token'

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 隐私模式下忽略 */
  }
  window.dispatchEvent(new Event('auth-changed'))
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response
  try {
    r = await fetch(`/api${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${getToken()}`,
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(0, '无法连接服务器')
  }
  if (r.status === 401) {
    setToken('')
    throw new ApiError(401, '登录已失效，请重新登录')
  }
  if (!r.ok) {
    let msg = `${r.status}`
    try {
      const body = await r.json()
      msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* 非 JSON 错误 */
    }
    throw new ApiError(r.status, msg)
  }
  return r.json()
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })

export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T,>(path: string) => api<T>(path, { method: 'DELETE' })

export function ago(s: string | null, now = Date.now()) {
  if (!s) return '—'
  const d = Math.round((now - Date.parse(s)) / 1000)
  if (d < 45) return '刚刚'
  if (d < 3600) return `${Math.round(d / 60)} 分钟前`
  if (d < 86400) return `${Math.round(d / 3600)} 小时前`
  return `${Math.round(d / 86400)} 天前`
}

export const fmtTime = (s: string | null) =>
  s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '—'

export const platformLabel: Record<Platform, string> = {
  telegram: 'Telegram',
  feishu: '飞书',
  wecom: '企业微信',
}

export const categoryLabel: Record<string, string> = {
  error: '调用报错', rate_limit: '429 限流', usage_request: '用量需求', other: '其他',
}
export const lookupLabel: Record<Lookup, string> = {
  request_id: '按 request_id 查日志', recent_errors: '近期错误与渠道分布', usage: '用量统计', channel: '渠道状态',
}
export const respLabel: Record<string, [string, 'info' | 'violet' | 'warn' | 'slate']> = {
  customer: ['客户侧', 'info'], us: ['我方', 'violet'], supplier: ['供应商', 'warn'], unknown: ['待定', 'slate'],
}

export type ModelCall = {
  id: string
  created_at: string
  finished_at: string | null
  incident_id: number | null
  group_id: number | null
  message_id: number | null
  kind: string
  step: number
  provider: 'claude' | 'jev' | string
  model: string
  status: 'running' | 'ok' | 'error'
  latency_ms: number
  input_tokens: number
  output_tokens: number
  error: string
}

export type ModelCallDetail = ModelCall & { request: string; response: string }

export type CallStats = { provider: string; calls: number; input_tokens: number; output_tokens: number; avg_latency_ms: number; errors: number }

export type LiveStage = { key: string; label: string; state: 'done' | 'active' | 'pending' | 'skipped'; at: string | null; detail: string }

export type Live = {
  status: Incident['status']
  escalated: boolean
  stages: LiveStage[]
  running: { id: string; kind: string; label: string; provider: string; model: string; step: number; started_at: string }[]
  latest: { kind: string; text: string; at: string } | null
  event_count: number
}

export const callKindLabel: Record<string, string> = {
  classify: '分诊', followup: '跟进判断', agent: '排查 Agent', investigate: '排查', integrate: '整合回复',
  compose: '撰写消息', stylize: '润色', read_image: '识别截图', summarize_conversation: '压缩对话',
  summarize_memory: '沉淀记忆', distill: '沉淀知识', learn_style: '学习风格', other: '其他',
}

/** 只显示时刻：12:38 / 12:38:15（补零，不受地区格式影响）。 */
export const clock = (s: string | null, seconds = false) => {
  if (!s) return ''
  const d = new Date(s)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}` + (seconds ? `:${p(d.getSeconds())}` : '')
}

export type AuditSource = 'http' | 'webhook' | 'agent' | 'scheduler' | 'system'

export type AuditLog = {
  id: number
  created_at: string
  trace_id: string
  source: AuditSource
  actor: string
  actor_id: number | null
  role: string
  action: string
  method: string
  path: string
  target: string
  status: number
  ok: boolean
  latency_ms: number
  db_count: number
  db_ms: number
  ip: string
  incident_id: number | null
  group_id: number | null
}

export type DbQuery = {
  id: number
  created_at: string
  trace_id: string
  db: 'app' | 'newapi' | string
  op: string
  caller: string
  statement: string
  params: string
  duration_ms: number
  rows: number
  error: string
  incident_id: number | null
  group_id: number | null
}

export type AuditDetail = AuditLog & { user_agent: string; request: string; detail: string; queries: DbQuery[] }

export type AuditStats = { source: AuditSource; count: number; failed: number; actors: number }[]

export type DbQueryStats = {
  dbs: { db: string; count: number; errors: number; avg_ms: number; p50_ms: number; p95_ms: number; max_ms: number }[]
  callers: { db: string; caller: string; count: number; total_ms: number; avg_ms: number; max_ms: number }[]
  sampled: number
}

export const auditSourceLabel: Record<AuditSource, string> = {
  http: '后台操作', webhook: '平台回调', agent: 'Agent', scheduler: '定时任务', system: '系统',
}

export const dbLabel: Record<string, string> = { app: '业务库', newapi: 'new-api 日志库' }

/** 毫秒：<1 显示一位小数，≥1000 显示秒。 */
export const fmtMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : ms < 10 ? `${ms.toFixed(1)}ms` : `${Math.round(ms)}ms`)
