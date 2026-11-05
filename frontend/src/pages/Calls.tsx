import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Bot, CircleAlert, Cpu, Timer } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, callKindLabel, type CallStats, type Group, type ModelCall } from '../api'
import { CallRow } from '../components/ModelCalls'
import { Button, Card, Empty, PageHeader, Segmented, Select, Skeleton, Stat } from '../ui'

const PAGE = 50
const fmtTok = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

/** 所有模型调用的留存：谁在什么时候为什么调了模型、花了多久、用了多少、说了什么。 */
export default function Calls() {
  const nav = useNavigate()
  const [provider, setProvider] = useState<'' | 'claude' | 'jev'>('')
  const [status, setStatus] = useState('')
  const [kind, setKind] = useState('')
  const { data: stats = [] } = useQuery({ queryKey: ['call-stats'], queryFn: () => api<CallStats[]>('/model-calls/stats'), refetchInterval: 10000 })
  const { data: groups = [] } = useQuery({ queryKey: ['groups'], queryFn: () => api<Group[]>('/groups') })
  const gname = new Map(groups.map((g) => [g.id, g.name]))
  const params = new URLSearchParams({ limit: String(PAGE), ...(provider && { provider }), ...(status && { status }), ...(kind && { kind }) })
  const q = useInfiniteQuery({
    queryKey: ['model-calls', provider, status, kind],
    queryFn: ({ pageParam }) => api<ModelCall[]>(`/model-calls?${params}${pageParam ? `&before=${encodeURIComponent(pageParam)}` : ''}`),
    initialPageParam: '',
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].created_at : undefined),
    refetchInterval: 3000,
  })
  const rows = q.data?.pages.flat() ?? []
  const sum = (f: (s: CallStats) => number) => stats.reduce((a, s) => a + f(s), 0)
  const calls = sum((s) => s.calls)
  const by = (p: string) => stats.find((s) => s.provider === p)

  return (
    <div>
      <PageHeader title="模型调用" desc="每一次调用 Claude / Jev 都完整留存：输入、输出、耗时、用量，以及它属于哪个事件。点开任意一行看原文。" />
      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="24 小时调用" value={calls} icon={<Cpu size={13} />} tone="accent"
          hint={`Claude ${by('claude')?.calls ?? 0} · Jev ${by('jev')?.calls ?? 0}`} />
        <Stat label="Token 用量" value={fmtTok(sum((s) => s.input_tokens + s.output_tokens))} icon={<Bot size={13} />}
          hint={`输入 ${fmtTok(sum((s) => s.input_tokens))} · 输出 ${fmtTok(sum((s) => s.output_tokens))}`} />
        <Stat label="平均耗时" value={`${((by('claude')?.avg_latency_ms ?? 0) / 1000).toFixed(1)}s`} icon={<Timer size={13} />}
          hint={`Jev ${by('jev')?.avg_latency_ms ?? 0}ms`} />
        <Stat label="失败" value={sum((s) => s.errors)} icon={<CircleAlert size={13} />} tone={sum((s) => s.errors) ? 'bad' : 'slate'}
          hint={calls ? `失败率 ${((sum((s) => s.errors) / calls) * 100).toFixed(1)}%` : '—'}
          active={status === 'error'} onClick={() => setStatus(status === 'error' ? '' : 'error')} />
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented value={provider} onChange={setProvider} options={[
          { value: '', label: '全部' }, { value: 'claude', label: 'Claude' }, { value: 'jev', label: 'Jev' },
        ]} />
        <Select className="w-36" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">全部用途</option>
          {Object.entries(callKindLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <Select className="w-28" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option><option value="ok">成功</option><option value="error">失败</option><option value="running">进行中</option>
        </Select>
      </div>
      {q.isLoading ? <Skeleton rows={4} /> : rows.length === 0 ? (
        <Empty icon={<Cpu size={18} />} title="还没有模型调用">客户在群里发消息后，这里会实时出现每一次调用。</Empty>
      ) : (
        <Card className="px-3 py-1">
          <ul>
            {rows.map((c) => (
              <CallRow key={c.id} c={c} extra={
                <span className="hidden min-w-0 truncate text-xs text-fg-3 md:inline">
                  {c.group_id ? gname.get(c.group_id) : ''}
                  {c.incident_id && <a className="ml-2 hover:text-accent" onClick={(e) => { e.stopPropagation(); nav(`/incidents?open=${c.incident_id}`) }}>#{c.incident_id}</a>}
                </span>} />
            ))}
          </ul>
          {q.hasNextPage && (
            <div className="py-3 text-center">
              <Button size="sm" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>{q.isFetchingNextPage ? '加载中…' : '加载更早的'}</Button>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
