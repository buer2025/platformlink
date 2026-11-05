import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { KeyRound, ShieldCheck, UserPlus } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { toast } from 'sonner'
import { api, patch, post, type User } from '../api'
import { Avatar, Badge, Button, Card, Field, Input, listItem, PageHeader, Select, Switch } from '../ui'

export default function UsersPage() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })
  const [f, setF] = useState({ username: '', display_name: '', password: '', role: 'agent' as User['role'] })
  const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : String(e))
  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] })
  const add = useMutation({
    mutationFn: () => post<User>('/users', f),
    onSuccess: (u) => { toast.success(`已创建 ${u.display_name}`, { description: `用户名 ${u.username}，把密码告诉他后请他自行修改` }); setF({ username: '', display_name: '', password: '', role: 'agent' }) },
    onError: fail, onSettled: refresh,
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<User> & { password?: string } }) => patch<User>(`/users/${id}`, body),
    onError: fail, onSettled: refresh,
  })
  return (
    <div>
      <PageHeader title="账号与值班" desc="每个技术支持一个账号，操作都会记在本人名下。值班中的同事会自动分到新事件（按手上未结事件最少）。显示名和「我方人员」里的姓名一致时，升级会 @ 到他的 IM 账号。" />
      <Card className="mb-6 p-4">
        <form className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_120px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <Field label="显示名"><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} placeholder="小王" /></Field>
          <Field label="用户名"><Input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="wang" autoComplete="off" /></Field>
          <Field label="初始密码" hint="至少 8 位"><Input type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" /></Field>
          <Field label="角色"><Select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as User['role'] })}>
            <option value="agent">技术支持</option><option value="admin">管理员</option></Select></Field>
          <Button variant="primary" icon={<UserPlus size={14} />} disabled={!f.username.trim() || !f.display_name.trim() || f.password.length < 8 || add.isPending}>添加</Button>
        </form>
      </Card>
      <Card className="divide-y divide-line overflow-hidden">
        <AnimatePresence initial={false}>
          {data.map((u) => (
            <motion.div layout {...listItem} key={u.id} className={clsx('flex flex-wrap items-center gap-3 px-5 py-3.5', !u.active && 'opacity-50')}>
              <Avatar name={u.display_name} size={32} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[13.5px] font-medium">{u.display_name}
                  {u.role === 'admin' && <Badge tone="accent"><ShieldCheck size={11} />管理员</Badge>}
                  {!u.active && <Badge>已停用</Badge>}
                </div>
                <div className="font-mono text-xs text-fg-3">{u.username}</div>
              </div>
              <label className="flex items-center gap-2 text-xs text-fg-2">
                <Switch checked={u.on_duty} onChange={(v) => update.mutate({ id: u.id, body: { on_duty: v } })} label="值班" />值班
              </label>
              <Button size="sm" variant="ghost" icon={<KeyRound size={13} />} onClick={() => {
                const pw = prompt(`给 ${u.display_name} 设置新密码（至少 8 位）`)
                if (pw) update.mutate({ id: u.id, body: { password: pw } }, { onSuccess: () => toast.success('密码已重置，他需要重新登录') })
              }}>重置密码</Button>
              <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: u.id, body: { active: !u.active } })}>{u.active ? '停用' : '启用'}</Button>
            </motion.div>
          ))}
        </AnimatePresence>
        {data.length === 0 && <p className="p-6 text-center text-[13px] text-fg-3">还没有账号。现在用的是管理口令，建议给每位同事建一个账号。</p>}
      </Card>
    </div>
  )
}
