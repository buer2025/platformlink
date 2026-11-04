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
  created_at: string
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

export const fmtTime = (s: string | null) =>
  s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '—'

export const platformLabel: Record<Platform, string> = {
  telegram: 'Telegram',
  feishu: '飞书',
  wecom: '企业微信',
}
