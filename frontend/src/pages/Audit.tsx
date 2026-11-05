import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { Bot, ChevronRight, CircleAlert, Clock, Database, ScrollText, Search, ShieldCheck, Timer, Webhook } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  api, auditSourceLabel, clock, dbLabel, fmtMs, fmtTime,
  type AuditDetail, type AuditLog, type AuditSource, type AuditStats, type DbQuery, type DbQueryStats,
} from '../api'
import { QueryRow } from '../components/DbQueries'
import { Badge, Button, Card, Empty, Input, PageHeader, Segmented, Select, Skeleton, Stat, Switch, type Tone } from '../ui'

const PAGE = 50
const sourceTone: Record<AuditSource, Tone> = { http: 'accent', webhook: 'info', agent: 'violet', scheduler: 'slate', system: 'slate' }

/** 操作审计：谁、什么时候、做了什么、结果如何、花了多久；以及数据库查询耗时。仅管理员可见。 */
export default function Audit() {
  const [tab, setTab] = useState<'ops' | 'db'>('ops')
  return (
    <div>
      <PageHeader title="操作审计" desc="后台每一次修改、登录、权限拒绝，平台回调、Agent 处理、定时任务与自动发送，以及数据库查询耗时，全部留痕。密码和密钥自动打码。" />
      <div className="mb-5">
        <Segmented value={tab} onChange={setTab} options={[
          { value: 'ops', label: '操作记录' }, { value: 'db', label: '数据库查询' },
        ]} />
      </div>
      {tab === 'ops' ? <Ops /> : <Queries />}
    </div>
  )
}

// ---------- 操作记录 ----------

function Ops() {
  const [source, setSource] = useState<'' | AuditSource>('')
  const [failed, setFailed] = useState(false)
  const [actor, setActor] = useState('')
  const [q, setQ] = useState('')
  const { data: stats = [] } = useQuery({ queryKey: ['audit-stats'], queryFn: () => api<AuditStats>('/audit/stats'), refetchInterval: 10000 })
  const by = (s: AuditSource) => stats.find((x) => x.source === s)
  const failedTotal = stats.reduce((a, s) => a + s.failed, 0)
  const params = new URLSearchParams({
    limit: String(PAGE), ...(source && { source }), ...(failed && { ok: 'false' }), ...(actor && { actor }), ...(q && { q }),
  })
  const list = useInfiniteQuery({
    queryKey: ['audit', source, failed, actor, q],
    queryFn: ({ pageParam }) => api<AuditLog[]>(`/audit?${params}${pageParam ? `&before_id=${pageParam}` : ''}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
    refetchInterval: 5000,
  })
  const rows = list.data?.pages.flat() ?? []
  const pick = (s: AuditSource) => setSource(source === s ? '' : s)

  return (
    <>
      <div className="mb-8 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="24 小时后台操作" value={by('http')?.count ?? 0} icon={<ShieldCheck size={13} />} tone="accent"
          hint={`${by('http')?.actors ?? 0} 人`} active={source === 'http'} onClick={() => pick('http')} />
        <Stat label="Agent 处理" value={by('agent')?.count ?? 0} icon={<Bot size={13} />}
          hint={`平台回调 ${by('webhook')?.count ?? 0}`} active={source === 'agent'} onClick={() => pick('agent')} />
        <Stat label="定时任务" value={by('scheduler')?.count ?? 0} icon={<Clock size={13} />}
          hint={`系统事件 ${by('system')?.count ?? 0}`} active={source === 'scheduler'} onClick={() => pick('scheduler')} />
        <Stat label="失败 / 拒绝" value={failedTotal} icon={<CircleAlert size={13} />} tone={failedTotal ? 'bad' : 'slate'}
          hint="含登录失败、权限拒绝" active={failed} onClick={() => setFailed(!failed)} />
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select className="w-32" value={source} onChange={(e) => setSource(e.target.value as '' | AuditSource)}>
          <option value="">全部来源</option>
          {Object.entries(auditSourceLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <Input className="w-32" placeholder="操作者" value={actor} onChange={(e) => setActor(e.target.value)} />
        <div className="relative min-w-0 flex-1 sm:max-w-64">
          <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-fg-3" />
          <Input className="pl-8" placeholder="搜索操作、对象、说明" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-xs text-fg-2"><Switch checked={failed} onChange={setFailed} label="只看失败" />只看失败</label>
      </div>
      {list.isLoading ? <Skeleton rows={5} /> : rows.length === 0 ? (
        <Empty icon={<ScrollText size={18} />} title="没有符合条件的记录">后台的修改、登录，以及 Agent 和定时任务做的事都会出现在这里。</Empty>
      ) : (
        <Card className="px-3 py-1">
          <ul>{rows.map((a) => <AuditRow key={a.id} a={a} />)}</ul>
          {list.hasNextPage && (
            <div className="py-3 text-center">
              <Button size="sm" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
                {list.isFetchingNextPage ? '加载中…' : '加载更早的'}
              </Button>
            </div>
          )}
        </Card>
      )}
    </>
  )
}

function AuditRow({ a }: { a: AuditLog }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="border-b border-line last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2.5 px-1 py-2.5 text-left text-[13px] hover:bg-panel-2/50">
        <ChevronRight size={14} className={clsx('shrink-0 text-fg-3 transition-transform duration-200', open && 'rotate-90')} />
        <Badge tone={sourceTone[a.source] ?? 'slate'} className="hidden sm:inline-flex">{auditSourceLabel[a.source] ?? a.source}</Badge>
        <span className="min-w-0 truncate">
          <span className="font-medium">{a.action}</span>
          {a.target && <span className="ml-1.5 font-mono text-xs text-fg-3">{a.target}</span>}
        </span>
        <span className="hidden min-w-0 truncate text-xs text-fg-2 md:inline">{a.actor}</span>
        <span className="ml-auto flex shrink-0 items-center gap-3 text-xs text-fg-3 tabular-nums">
          {!a.ok && <span className="inline-flex items-center gap-1 text-bad"><CircleAlert size={12} />{a.status || '失败'}</span>}
          <span className="hidden w-24 text-right lg:inline" title="这次操作执行的 SQL 条数 / 数据库耗时">
            {a.db_count ? <><Database size={11} className="mr-1 inline" />{a.db_count} · {fmtMs(a.db_ms)}</> : ''}
          </span>
          <span className="w-12 text-right">{fmtMs(a.latency_ms)}</span>
          <span className="w-16 text-right" title={fmtTime(a.created_at)}>{clock(a.created_at, true)}</span>
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden px-1">
            <AuditDetailView id={a.id} />
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  if (children === '' || children == null) return null
  return (
    <div className="flex min-w-0 gap-2 text-xs">
      <dt className="w-16 shrink-0 text-fg-3">{label}</dt>
      <dd className="min-w-0 break-all text-fg-2">{children}</dd>
    </div>
  )
}

function pretty(raw: string) {
  try { return JSON.stringify(JSON.parse(raw), null, 2) } catch { return raw }
}

function AuditDetailView({ id }: { id: number }) {
  const nav = useNavigate()
  const { data: d } = useQuery({ queryKey: ['audit', id], queryFn: () => api<AuditDetail>(`/audit/${id}`) })
  if (!d) return <div className="py-3 text-xs text-fg-3">加载中…</div>
  return (
    <div className="space-y-3 pt-1 pb-3">
      <dl className="grid gap-1.5 sm:grid-cols-2">
        <Fact label="时间">{fmtTime(d.created_at)}</Fact>
        <Fact label="操作者">{`${d.actor || '—'}${d.role ? `（${d.role === 'admin' ? '管理员' : '技术支持'}）` : ''}`}</Fact>
        <Fact label="来源">{auditSourceLabel[d.source] ?? d.source}</Fact>
        <Fact label="结果">{d.ok ? `成功${d.status ? `（${d.status}）` : ''}` : `失败${d.status ? `（${d.status}）` : ''}`}</Fact>
        <Fact label="请求">{d.method ? `${d.method} ${d.path}` : ''}</Fact>
        <Fact label="IP">{d.ip}</Fact>
        <Fact label="耗时">{`${fmtMs(d.latency_ms)}，其中数据库 ${d.db_count} 条 SQL / ${fmtMs(d.db_ms)}`}</Fact>
        <Fact label="事件">{d.incident_id ? <a className="text-accent hover:underline" onClick={() => nav(`/incidents?open=${d.incident_id}`)}>#{d.incident_id}</a> : ''}</Fact>
        <Fact label="客户端">{d.user_agent}</Fact>
      </dl>
      {d.detail && <Section title="说明">{d.detail}</Section>}
      {d.request && <Section title="参数（敏感字段已打码）">{pretty(d.request)}</Section>}
      {d.queries.length > 0 && (
        <div>
          <div className="mb-1 text-[10.5px] font-medium tracking-wide text-fg-3 uppercase">单独记录的查询（日志库全部 + 业务库慢查询）</div>
          <ul className="rounded-lg border border-line px-2">{d.queries.map((q) => <QueryRow key={q.id} q={q} />)}</ul>
        </div>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: string }) {
  return (
    <div>
      <div className="mb-1 text-[10.5px] font-medium tracking-wide text-fg-3 uppercase">{title}</div>
      <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-panel p-2.5 font-mono text-[11.5px] leading-relaxed whitespace-pre-wrap text-fg-2">{children}</pre>
    </div>
  )
}

// ---------- 数据库查询 ----------

function Queries() {
  const nav = useNavigate()
  const [db, setDb] = useState<'' | 'newapi' | 'app'>('')
  const [minMs, setMinMs] = useState('')
  const [errors, setErrors] = useState(false)
  const { data: stats } = useQuery({ queryKey: ['db-stats'], queryFn: () => api<DbQueryStats>('/db-queries/stats'), refetchInterval: 10000 })
  const s = (name: string) => stats?.dbs.find((x) => x.db === name)
  const params = new URLSearchParams({ limit: String(PAGE), ...(db && { db }), ...(minMs && { min_ms: minMs }), ...(errors && { errors: 'true' }) })
  const list = useInfiniteQuery({
    queryKey: ['db-queries', db, minMs, errors],
    queryFn: ({ pageParam }) => api<DbQuery[]>(`/db-queries?${params}${pageParam ? `&before_id=${pageParam}` : ''}`),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
    refetchInterval: 5000,
  })
  const rows = list.data?.pages.flat() ?? []
  const nn = s('newapi'), app = s('app')
  const errs = (stats?.dbs ?? []).reduce((a, x) => a + x.errors, 0)

  return (
    <>
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="24 小时日志库查询" value={nn?.count ?? 0} icon={<Database size={13} />} tone="accent"
          hint={nn ? `平均 ${fmtMs(nn.avg_ms)}` : '未配置或还没有查询'} active={db === 'newapi'} onClick={() => setDb(db === 'newapi' ? '' : 'newapi')} />
        <Stat label="日志库 P95" value={nn ? fmtMs(nn.p95_ms) : '—'} icon={<Timer size={13} />}
          hint={nn ? `P50 ${fmtMs(nn.p50_ms)} · 最慢 ${fmtMs(nn.max_ms)}` : '—'} />
        <Stat label="业务库慢查询" value={app?.count ?? 0} icon={<Webhook size={13} />} tone={app?.count ? 'warn' : 'slate'}
          hint={app ? `最慢 ${fmtMs(app.max_ms)}` : '没有超过阈值的查询'} active={db === 'app'} onClick={() => setDb(db === 'app' ? '' : 'app')} />
        <Stat label="查询出错" value={errs} icon={<CircleAlert size={13} />} tone={errs ? 'bad' : 'slate'}
          hint="连接失败、语法、超时" active={errors} onClick={() => setErrors(!errors)} />
      </div>
      {!!stats?.callers.length && (
        <Card className="mb-6 px-4 py-3">
          <div className="mb-2 text-xs font-medium text-fg-2">最耗时的调用方（24 小时）</div>
          <table className="w-full table-fixed text-xs tabular-nums">
            <colgroup><col /><col className="hidden w-28 sm:table-column" /><col className="w-12" /><col className="w-16" /><col className="hidden w-16 sm:table-column" /><col className="w-16" /></colgroup>
            <thead className="text-fg-3">
              <tr><th className="pb-1 text-left font-normal">调用方</th><th className="hidden pb-1 text-left font-normal sm:table-cell">库</th>
                <th className="pb-1 text-right font-normal">次数</th><th className="pb-1 text-right font-normal">平均</th>
                <th className="hidden pb-1 text-right font-normal sm:table-cell">最慢</th><th className="pb-1 text-right font-normal">累计</th></tr>
            </thead>
            <tbody>
              {stats.callers.map((c) => (
                <tr key={`${c.db}${c.caller}`} className="border-t border-line">
                  <td className="truncate py-1.5 pr-2 font-mono" title={c.caller}>{c.caller}</td>
                  <td className="hidden py-1.5 text-fg-3 sm:table-cell">{dbLabel[c.db] ?? c.db}</td>
                  <td className="py-1.5 text-right">{c.count}</td>
                  <td className="py-1.5 text-right">{fmtMs(c.avg_ms)}</td>
                  <td className="hidden py-1.5 text-right sm:table-cell">{fmtMs(c.max_ms)}</td>
                  <td className="py-1.5 text-right font-medium">{fmtMs(c.total_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented value={db} onChange={setDb} options={[
          { value: '', label: '全部' }, { value: 'newapi', label: '日志库' }, { value: 'app', label: '业务库' },
        ]} />
        <Select className="w-28" value={minMs} onChange={(e) => setMinMs(e.target.value)}>
          <option value="">全部耗时</option><option value="10">≥ 10ms</option><option value="100">≥ 100ms</option><option value="1000">≥ 1s</option>
        </Select>
        <label className="flex items-center gap-2 text-xs text-fg-2"><Switch checked={errors} onChange={setErrors} label="只看出错" />只看出错</label>
      </div>
      {list.isLoading ? <Skeleton rows={5} /> : rows.length === 0 ? (
        <Empty icon={<Database size={18} />} title="还没有查询记录">Agent 排查时查 new-api 日志库的每一次查询都会出现在这里；业务库只记超过阈值的慢查询。</Empty>
      ) : (
        <Card className="px-3 py-1">
          <ul>
            {rows.map((q) => (
              <QueryRow key={q.id} q={q} extra={q.incident_id ? (
                <a className="hidden text-xs text-fg-3 hover:text-accent md:inline" onClick={(e) => { e.stopPropagation(); nav(`/incidents?open=${q.incident_id}`) }}>#{q.incident_id}</a>
              ) : undefined} />
            ))}
          </ul>
          {list.hasNextPage && (
            <div className="py-3 text-center">
              <Button size="sm" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
                {list.isFetchingNextPage ? '加载中…' : '加载更早的'}
              </Button>
            </div>
          )}
        </Card>
      )}
    </>
  )
}
