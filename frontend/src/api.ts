export type Group = {
  id: number
  platform: 'telegram' | 'feishu' | 'wecom'
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
  status: 'pending' | 'needs_approval' | 'sent' | 'cancelled' | 'failed'
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
  status: string
  created_at: string
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
