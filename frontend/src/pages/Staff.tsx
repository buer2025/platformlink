import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Users } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { toast } from 'sonner'
import { api, del, platformLabel, post, type Platform, type Staff } from '../api'
import { Badge, Button, Card, Empty, ErrorText, Input, listItem, PageHeader, Select } from '../ui'

export default function StaffPage() {
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['staff'], queryFn: () => api<Staff[]>('/staff') })
  const [f, setF] = useState({ name: '', platform: 'telegram' as Platform, account: '' })
  const add = useMutation({
    mutationFn: () => post<Staff>('/staff', f),
    onSuccess: (s) => { setF({ ...f, name: '', account: '' }); toast.success(`已添加 ${s.name}`) },
    onSettled: () => qc.invalidateQueries({ queryKey: ['staff'] }),
  })
  const remove = useMutation({
    mutationFn: (id: number) => del(`/staff/${id}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['staff'] }),
  })
  return (
    <div>
      <PageHeader title="我方人员" desc="登记技术支持在各平台的账号（Telegram 用户名 / 飞书 open_id / 企业微信 userid），他们的发言不会被当成客户问题。" />
      <Card className="mb-4 p-3">
        <form className="grid gap-2 sm:grid-cols-[1fr_140px_1fr_auto]" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
          <Input placeholder="姓名" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <Select value={f.platform} onChange={(e) => setF({ ...f, platform: e.target.value as Platform })}>
            {Object.entries(platformLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Input placeholder="平台账号" value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })} />
          <Button variant="primary" disabled={!f.name.trim() || !f.account.trim() || add.isPending}>添加</Button>
        </form>
        <div className="mt-1 px-1"><ErrorText error={add.error} /></div>
      </Card>
      {data.length === 0 ? <Empty icon={<Users size={18} />} title="还没有登记我方人员" /> : (
        <Card className="divide-y divide-line overflow-hidden">
          <AnimatePresence initial={false}>
            {data.map((s) => (
              <motion.div layout {...listItem} key={s.id} className="group flex items-center gap-3 px-4 py-2.5 text-[13px]">
                <span className="grid size-7 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{s.name.slice(0, 1)}</span>
                <span className="font-medium">{s.name}</span>
                <Badge>{platformLabel[s.platform]}</Badge>
                <span className="font-mono text-xs text-fg-2">{s.account}</span>
                <Button variant="ghost" size="sm" className="ml-auto opacity-60 group-hover:opacity-100" aria-label={`删除 ${s.name}`}
                  onClick={() => confirm(`删除 ${s.name}？`) && remove.mutate(s.id)}><Trash2 size={13} />删除</Button>
              </motion.div>
            ))}
          </AnimatePresence>
        </Card>
      )}
    </div>
  )
}
