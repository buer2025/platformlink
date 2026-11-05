import clsx from 'clsx'
import { X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import {
  forwardRef, useEffect, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type KeyboardEvent, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react'

export const spring = { type: 'spring', stiffness: 520, damping: 38, mass: 0.8 } as const

/* ---------- 按钮 ---------- */

type Variant = 'primary' | 'plain' | 'ghost' | 'danger'
const variants: Record<Variant, string> = {
  primary: 'bg-accent text-accent-fg hover:brightness-110 shadow-[0_1px_2px_rgb(0_0_0/0.15),inset_0_1px_0_rgb(255_255_255/0.18)]',
  plain: 'bg-panel text-fg border border-line hover:bg-panel-2 hover:border-line-strong',
  ghost: 'text-fg-2 hover:bg-panel-2 hover:text-fg',
  danger: 'bg-panel text-bad border border-line hover:bg-bad-soft hover:border-bad/30',
}

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: 'sm' | 'md'; kbd?: string; icon?: ReactNode
}>(function Button({ variant = 'plain', size = 'md', kbd, icon, className, children, ...p }, ref) {
  return (
    <button ref={ref} {...p} className={clsx(
      'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap select-none',
      'transition-[background,border,filter,transform] duration-150 active:scale-[0.97]',
      'disabled:pointer-events-none disabled:opacity-45',
      size === 'sm' ? 'h-7 px-2.5 text-xs' : 'h-8 px-3 text-[13px]',
      variants[variant], className)}>
      {icon}
      {children}
      {kbd && <Kbd className={variant === 'primary' ? 'border-white/25 bg-white/10 text-white/80' : ''}>{kbd}</Kbd>}
    </button>
  )
})

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd className={clsx(
      'ml-0.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-line bg-panel-2 px-1',
      'font-sans text-[10px] font-medium text-fg-3', className)}>
      {children}
    </kbd>
  )
}

/* ---------- 表单 ---------- */

const field = 'w-full rounded-lg border border-line bg-panel px-2.5 text-[13px] text-fg placeholder:text-fg-3 ' +
  'transition-[border,box-shadow] duration-150 hover:border-line-strong ' +
  'focus:border-accent focus:outline-none focus:ring-3 focus:ring-accent/15'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...p }, ref) {
    return <input ref={ref} {...p} className={clsx(field, 'h-8', className)} />
  })

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...p }, ref) {
    return <textarea ref={ref} {...p} className={clsx(field, 'py-2 leading-relaxed', className)} />
  })

export function Select({ className, ...p }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...p} className={clsx(field, 'h-8 pr-7', className)} />
}

export function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={clsx('block', wide && 'sm:col-span-2')}>
      <span className="text-xs font-medium text-fg-2">{label}</span>
      {hint && <span className="ml-1.5 text-[11px] text-fg-3">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  )
}

/** 标签输入：回车 / 逗号添加，退格删除最后一个。 */
export function TagInput({ value, onChange, placeholder, tone = 'slate' }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string; tone?: Tone
}) {
  const [draft, setDraft] = useState('')
  const add = (raw: string) => {
    const items = raw.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)
    const next = [...value]
    for (const x of items) if (!next.includes(x)) next.push(x)
    onChange(next)
    setDraft('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.key === 'Enter' || e.key === ',') && draft.trim()) { e.preventDefault(); add(draft) }
    else if (e.key === 'Backspace' && !draft && value.length) onChange(value.slice(0, -1))
  }
  return (
    <div className={clsx(field, 'flex min-h-8 flex-wrap items-center gap-1 py-1')}>
      <AnimatePresence initial={false}>
        {value.map((v) => (
          <motion.span key={v} layout initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }} transition={spring} className="max-w-full">
            <Badge tone={tone} className="h-auto min-h-5 max-w-full py-0.5 leading-snug whitespace-normal">
              {v}
              <button type="button" aria-label={`移除 ${v}`} className="-mr-0.5 ml-0.5 opacity-60 hover:opacity-100"
                onClick={() => onChange(value.filter((x) => x !== v))}><X size={11} /></button>
            </Badge>
          </motion.span>
        ))}
      </AnimatePresence>
      <input className="min-w-20 flex-1 bg-transparent py-0.5 outline-none placeholder:text-fg-3"
        value={draft} placeholder={value.length ? '' : placeholder}
        onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} onBlur={() => draft.trim() && add(draft)} />
    </div>
  )
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={clsx('relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-200',
        checked ? 'bg-accent' : 'bg-line-strong')}>
      <motion.span layout transition={spring}
        className={clsx('block size-4 rounded-full bg-white shadow', checked ? 'ml-[18px]' : 'ml-0.5')} />
    </button>
  )
}

/* ---------- 展示 ---------- */

export type Tone = 'slate' | 'accent' | 'ok' | 'warn' | 'bad' | 'info' | 'violet'
const tones: Record<Tone, string> = {
  slate: 'bg-panel-2 text-fg-2 border-line',
  accent: 'bg-accent-soft text-accent border-transparent',
  ok: 'bg-ok-soft text-ok border-transparent',
  warn: 'bg-warn-soft text-warn border-transparent',
  bad: 'bg-bad-soft text-bad border-transparent',
  info: 'bg-info-soft text-info border-transparent',
  violet: 'bg-violet-soft text-violet border-transparent',
}

export function Badge({ children, tone = 'slate', dot, className }: { children: ReactNode; tone?: Tone; dot?: boolean; className?: string }) {
  return (
    <span className={clsx('inline-flex h-5 items-center gap-1 rounded-md border px-1.5 text-[11px] font-medium whitespace-nowrap',
      tones[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export const scoreTone = (s: number): Tone => (s >= 90 ? 'ok' : s >= 70 ? 'warn' : 'bad')

/** 准确率环：一眼看出能不能自动发。 */
export function ScoreRing({ score, size = 34 }: { score: number; size?: number }) {
  const r = (size - 4) / 2
  const c = 2 * Math.PI * r
  const color = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)' }[scoreTone(score) as 'ok']
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title={`准确率 ${score}`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={3} />
        <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={3}
          strokeLinecap="round" strokeDasharray={c} initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - score / 100) }} transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }} />
      </svg>
      <span className="absolute inset-0 grid place-items-center font-semibold tabular-nums tracking-tighter"
        style={{ color, fontSize: Math.max(9, size * (score >= 100 ? 0.27 : 0.32)) }}>{score}</span>
    </div>
  )
}

export function Card({ children, className, ...p }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return <div {...p} className={clsx('surface rounded-2xl', className)}>{children}</div>
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
      className="flex flex-col items-center rounded-xl border border-dashed border-line px-6 py-12 text-center">
      {icon && <div className="mb-3 grid size-10 place-items-center rounded-xl bg-panel-2 text-fg-3">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="mt-1 max-w-sm text-[13px] text-fg-3">{children}</div>}
    </motion.div>
  )
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2.5">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-[76px] animate-pulse rounded-xl border border-line bg-panel" style={{ animationDelay: `${i * 120}ms` }} />
      ))}
    </div>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null
  return <p className="text-[13px] text-bad">{error instanceof Error ? error.message : String(error)}</p>
}

export function PageHeader({ title, desc, children }: { title: string; desc?: string; children?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end gap-4">
      <div className="min-w-0 flex-1">
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {desc && <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-fg-3">{desc}</p>}
      </div>
      {children}
    </div>
  )
}

/** 分段切换（带滑块动画）。 */
export function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[]
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel-2 p-0.5">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={clsx('relative h-7 rounded-md px-2.5 text-xs font-medium transition-colors',
            value === o.value ? 'text-fg' : 'text-fg-3 hover:text-fg-2')}>
          {value === o.value && (
            <motion.span layoutId={`seg-${options.map((x) => x.value).join()}`} transition={spring}
              className="absolute inset-0 rounded-md border border-line bg-panel shadow-card" />
          )}
          <span className="relative flex items-center gap-1">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

/* ---------- 抽屉 ---------- */

export function Drawer({ open, onClose, title, subtitle, children, footer, width = 560 }: {
  open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode
  children: ReactNode; footer?: ReactNode; width?: number
}) {
  useEffect(() => {
    if (!open) return
    const h = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" onClick={onClose}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }} />
          <motion.aside role="dialog" aria-modal
            className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-panel shadow-pop sm:inset-y-2 sm:right-2 sm:rounded-2xl sm:border"
            style={{ maxWidth: width }}
            initial={{ x: '104%' }} animate={{ x: 0 }} exit={{ x: '104%' }} transition={spring}>
            <header className="flex items-start gap-3 border-b border-line px-5 py-4">
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold">{title}</div>
                {subtitle && <div className="mt-1 text-xs text-fg-3">{subtitle}</div>}
              </div>
              <Button variant="ghost" size="sm" aria-label="关闭" onClick={onClose}><X size={15} /><Kbd>Esc</Kbd></Button>
            </header>
            <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
            {footer && <footer className="flex flex-wrap items-center gap-2 border-t border-line px-5 py-3">{footer}</footer>}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}

/** 列表项依次入场。 */
export const listItem = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, x: -24, transition: { duration: 0.18 } },
  transition: spring,
}

/** 由名字生成的头像：同一个群永远是同一种颜色。 */
const AVATAR = ['#3d4fd6', '#1f7a4d', '#a8570c', '#6b4fc8', '#2b6cb0', '#b4446c', '#4d7c0f', '#0e7490']
export function Avatar({ name, size = 36, square }: { name: string; size?: number; square?: boolean }) {
  let h = 0
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  const c = AVATAR[h % AVATAR.length]
  return (
    <span className={clsx('grid shrink-0 place-items-center font-semibold text-white select-none', square ? 'rounded-xl' : 'rounded-full')}
      style={{
        width: size, height: size, fontSize: size * 0.4,
        background: `linear-gradient(145deg, color-mix(in oklab, ${c} 82%, white), ${c})`,
        boxShadow: 'inset 0 1px 0 rgb(255 255 255 / 0.25), 0 1px 2px rgb(0 0 0 / 0.12)',
      }}>
      {name.replace(/[·\s]/g, '').slice(0, 1)}
    </span>
  )
}

/** 统计卡：大数字 + 一句话。 */
export function Stat({ label, value, hint, tone = 'slate', icon, active, onClick }: {
  label: string; value: number | string; hint?: string; tone?: Tone; icon?: ReactNode; active?: boolean; onClick?: () => void
}) {
  const color: Record<Tone, string> = {
    slate: 'text-fg-3', accent: 'text-accent', ok: 'text-ok', warn: 'text-warn', bad: 'text-bad', info: 'text-info', violet: 'text-violet',
  }
  return (
    <button onClick={onClick} className={clsx('surface group rounded-2xl px-4 py-3.5 text-left transition-transform duration-200 hover:-translate-y-0.5',
      active && 'ring-2 ring-accent/30')}>
      <div className="flex items-center gap-1.5 text-xs text-fg-3"><span className={color[tone]}>{icon}</span>{label}</div>
      <motion.div key={value} initial={{ y: 6, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={spring}
        className="tnum mt-1.5 text-[26px] leading-none font-semibold tracking-tight">{value}</motion.div>
      {hint && <div className="mt-1.5 text-[11.5px] text-fg-3">{hint}</div>}
    </button>
  )
}
