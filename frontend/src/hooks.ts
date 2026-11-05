import { useEffect, useLayoutEffect, useRef, useState } from 'react'

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))

/**
 * 全局快捷键。键写法：'j'、'shift+?'、'mod+k'（mod = ⌘ / Ctrl）、'g p'（先按 g 再按 p）。
 * 输入框聚焦时只响应带 mod 的组合键。
 */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>, enabled = true) {
  const ref = useRef(map)
  useLayoutEffect(() => { ref.current = map })
  useEffect(() => {
    if (!enabled) return
    const state = { prefix: '' }
    const state2: { timer?: ReturnType<typeof setTimeout> } = {}
    const h = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (isTyping(e.target) && !mod) return
      if (document.querySelector('[role=dialog][aria-modal]') && !mod && e.key !== 'Escape' && !e.defaultPrevented) {
        // 弹层打开时，只把快捷键交给弹层自己处理的组合
        if (!Object.keys(ref.current).some((k) => k.startsWith('drawer:'))) return
      }
      const key = (mod ? 'mod+' : '') + (e.shiftKey && e.key.length > 1 ? 'shift+' : '') + e.key.toLowerCase()
      const combo = state.prefix ? `${state.prefix} ${key}` : key
      const fn = ref.current[combo] ?? ref.current[`drawer:${combo}`]
      if (fn) {
        e.preventDefault()
        state.prefix = ''
        fn(e)
        return
      }
      if (!mod && Object.keys(ref.current).some((k) => k.startsWith(`${key} `))) {
        state.prefix = key
        clearTimeout(state2.timer)
        state2.timer = setTimeout(() => { state.prefix = '' }, 900)
      } else state.prefix = ''
    }
    window.addEventListener('keydown', h)
    return () => { window.removeEventListener('keydown', h); clearTimeout(state2.timer) }
  }, [enabled])
}

export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(t)
  }, [ms])
  return now
}

type Theme = 'system' | 'light' | 'dark'
const THEME_KEY = 'platformlink.theme'

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem(THEME_KEY) as Theme) || 'system' } catch { return 'system' }
  })
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') el.removeAttribute('data-theme')
    else el.setAttribute('data-theme', theme)
    try { localStorage.setItem(THEME_KEY, theme) } catch { /* 忽略 */ }
  }, [theme])
  const cycle = () => setTheme((t) => (t === 'system' ? 'dark' : t === 'dark' ? 'light' : 'system'))
  return { theme, setTheme, cycle }
}

/** 在列表中用 j/k 移动选中项。 */
export function useListNav<T extends { id: number }>(rows: T[]) {
  const [sel, setSel] = useState<number | null>(null)
  const idx = rows.findIndex((r) => r.id === sel)
  const current = idx >= 0 ? rows[idx] : undefined
  const move = (d: number) => {
    if (!rows.length) return
    const next = rows[Math.min(rows.length - 1, Math.max(0, (idx < 0 ? (d > 0 ? -1 : rows.length) : idx) + d))]
    setSel(next.id)
    requestAnimationFrame(() => document.querySelector(`[data-row="${next.id}"]`)?.scrollIntoView({ block: 'nearest' }))
  }
  return { sel, setSel, current, move }
}
