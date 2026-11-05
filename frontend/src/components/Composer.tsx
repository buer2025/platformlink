import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CornerUpLeft, Send, Sparkles, Wand2, X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { post, type Message } from '../api'
import { Button, Kbd, Textarea } from '../ui'

/**
 * 半 AI 半人工：自己写直接发；或让 AI 起草 / 按群风格润色后再发。
 * 发出后 AI 会让位一段时间，避免和人说重复的话。
 */
export function Composer({ groupId, groupName, incidentId, quote, onClearQuote }: {
  groupId: number; groupName: string; incidentId?: number
  quote?: Message | null; onClearQuote?: () => void
}) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const ta = useRef<HTMLTextAreaElement>(null)
  const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : String(e))
  const draft = useMutation({
    mutationFn: () => post<{ text: string }>(`/groups/${groupId}/draft`, { incident_id: incidentId, intent: text }),
    onSuccess: (r) => { setText(r.text); ta.current?.focus() },
    onError: fail,
  })
  const polish = useMutation({
    mutationFn: () => post<{ text: string }>(`/groups/${groupId}/style/preview`, { text }),
    onSuccess: (r) => { setText(r.text); ta.current?.focus() },
    onError: fail,
  })
  const send = useMutation({
    mutationFn: () => post(`/groups/${groupId}/send`, {
      text, incident_id: incidentId, reply_to_message_id: quote?.id,
    }),
    onSuccess: () => {
      setText(''); onClearQuote?.()
      toast.success(`已发送到 ${groupName}`, { description: 'AI 会暂停自动发送 30 分钟，避免和你重复' })
    },
    onError: fail,
    onSettled: () => {
      for (const k of ['outbound', 'incidents', 'events', 'incident-msgs']) qc.invalidateQueries({ queryKey: [k] })
    },
  })
  const busy = draft.isPending || polish.isPending || send.isPending
  return (
    <div className="surface rounded-2xl p-3">
      <AnimatePresence>
        {quote && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            className="mb-2 flex items-start gap-2 rounded-lg bg-panel-2 px-2.5 py-1.5 text-xs text-fg-2">
            <CornerUpLeft size={13} className="mt-0.5 shrink-0 text-fg-3" />
            <span className="line-clamp-2 flex-1"><span className="text-fg-3">回复 {quote.sender}：</span>{quote.text}</span>
            <button aria-label="取消引用" onClick={onClearQuote} className="text-fg-3 hover:text-fg"><X size={13} /></button>
          </motion.div>
        )}
      </AnimatePresence>
      <Textarea ref={ta} rows={3} value={text} disabled={busy}
        className="border-0 bg-transparent px-1 shadow-none ring-0 hover:border-0 focus:ring-0"
        placeholder={`以机器人身份发到「${groupName}」… 也可以只写个意思，让 AI 帮你写`}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && text.trim()) { e.preventDefault(); send.mutate() } }} />
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="ghost" icon={<Sparkles size={13} />} disabled={busy} onClick={() => draft.mutate()}
          title="根据对话和进展起草；输入框里写了意思就按你的意思写">
          {draft.isPending ? '起草中…' : 'AI 帮我写'}
        </Button>
        <Button size="sm" variant="ghost" icon={<Wand2 size={13} />} disabled={busy || !text.trim()} onClick={() => polish.mutate()}>
          {polish.isPending ? '润色中…' : '按群风格润色'}
        </Button>
        <span className="ml-auto hidden text-[11px] text-fg-3 sm:inline">原样发送，不经 AI 改写 <Kbd>⌘</Kbd><Kbd>↵</Kbd></span>
        <Button size="sm" variant="primary" icon={<Send size={13} />} disabled={busy || !text.trim()} onClick={() => send.mutate()}>
          {send.isPending ? '发送中…' : '发送'}
        </Button>
      </div>
    </div>
  )
}
