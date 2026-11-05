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
  owner: string
  backup_owner: string
  tags: string[]
  api_users: string[]
  channel_ids: number[]
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
  knowledge_ids: number[]
  resolved_at: string | null
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
