// 端到端自检：node e2e/smoke.cjs [baseURL] [adminToken]
const { chromium } = require('playwright')
const BASE = process.argv[2] || 'http://localhost:8000'
const TOKEN = process.argv[3] || 'e2e-token'
const SHOTS = process.env.SHOTS || ''
let failures = 0
const check = (ok, msg) => { console.log(`${ok ? '✓' : '✗'} ${msg}`); if (!ok) failures++ }

async function api(path, body, method) {
  const r = await fetch(BASE + '/api' + path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
    body: body ? JSON.stringify(body) : undefined,
  })
  return r.json()
}

;(async () => {
  // 准备数据
  await api('/simulate', { chat_id: 'cust', chat_name: '客户A群', text: 'hi' })
  await api('/simulate', { chat_id: 'sup', chat_name: '供应商Y群', text: 'hi' })
  const groups = await api('/groups')
  const cust = groups.find((g) => g.external_id === 'cust')
  const sup = groups.find((g) => g.external_id === 'sup')
  await api(`/groups/${cust.id}`, { automation: 'auto' }, 'PATCH')
  await api(`/groups/${sup.id}`, { kind: 'supplier', automation: 'auto', models: ['seedance'], contacts: ['yy'] }, 'PATCH')
  await api('/simulate', { chat_id: 'cust', text: 'seedance 一直 429 request_id: req-e2e-0001' })

  const browser = await chromium.launch()
  const consoleErrors = []
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } })
  page.on('pageerror', (e) => consoleErrors.push(String(e)))
  page.on('console', (m) => m.type() === 'error' && !m.text().startsWith('Failed to load resource') && consoleErrors.push(m.text()))
  page.on('dialog', (d) => d.accept())

  // 登录
  await page.goto(BASE + '/')
  await page.getByPlaceholder('服务端 ADMIN_TOKEN').fill('wrong')
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByText('口令错误').waitFor()
  check(true, '错误口令提示')
  await page.getByPlaceholder('服务端 ADMIN_TOKEN').fill(TOKEN)
  await page.getByRole('button', { name: '登录' }).click()
  await page.getByRole('heading', { name: /待处理/ }).waitFor()
  check(true, '登录进入预发队列')

  // 预发队列
  const cards = page.getByTestId('outbound')
  await cards.first().waitFor()
  const n = await cards.count()
  check(n >= 3, `预发队列显示 ${n} 条（安抚/客户回复/供应商转交）`)
  check(await page.getByText('供应商转交').first().isVisible(), '供应商转交单进入队列')
  check(await page.getByText('@yy').first().isVisible(), '转交单 @ 供应商对接人')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/presend.png` })

  // 修改一条
  const reply = cards.filter({ hasText: '客户回复' }).first()
  await reply.getByRole('button', { name: '修改' }).click()
  await reply.locator('textarea').fill('人工修改后的回复')
  await reply.getByRole('button', { name: /保存/ }).click()
  await page.getByText('人工修改后的回复').waitFor()
  check(true, '修改消息后保存')
  // 取消一条
  const ticket = cards.filter({ hasText: '供应商转交' }).first()
  await ticket.getByRole('button', { name: '取消' }).click()
  await page.getByRole('heading', { name: '历史' }).waitFor()
  check(await cards.filter({ hasText: '已取消' }).count() >= 1, '取消消息进入历史')
  // 立即发送（未配置 bot token，应显示失败与重新发送）
  await cards.filter({ hasText: '人工修改后的回复' }).getByRole('button', { name: '立即发送' }).click()
  await page.getByRole('button', { name: '重新发送' }).first().waitFor({ timeout: 20000 })
  check(true, '发送失败时显示原因并可重新发送')

  // 事件中心
  await page.getByRole('link', { name: '事件中心' }).click()
  await page.getByText('429 限流').first().waitFor()
  await page.getByText('429 限流').first().click()
  await page.getByText('req-e2e-0001', { exact: true }).waitFor()
  check(true, '事件详情显示 request_id')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/incidents.png` })
  await page.getByRole('button', { name: '标记已解决' }).click()
  await page.getByText('暂无事件').waitFor()
  check(true, '标记已解决后从未解决列表消失')

  // 群与标记
  await page.getByRole('link', { name: '群与标记' }).click()
  await page.getByText('客户A群').click()
  await page.getByPlaceholder('王总 / 哥 / 老师').fill('王总')
  await page.getByPlaceholder('正式 / 轻松 / 熟络 / 英文').fill('熟络')
  await page.getByPlaceholder('VIP / KA / 主力供应商').fill('VIP')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/groups.png`, fullPage: true })
  await page.getByRole('button', { name: '保存' }).click()
  await page.locator('span', { hasText: /^VIP$/ }).waitFor()
  const saved = (await api('/groups')).find((g) => g.id === cust.id)
  check(saved.style.address === '王总' && saved.level === 'VIP', '风格标记与等级保存到后端')
  await page.getByPlaceholder('搜索群名 / 模型 / 负责人').fill('seedance')
  await page.getByText('1 / 2 个群').waitFor()
  check(true, '按模型搜索群')

  // 我方人员
  await page.getByRole('link', { name: '我方人员' }).click()
  await page.getByPlaceholder('姓名').fill('小王')
  await page.getByPlaceholder('平台账号').fill('wang_support')
  await page.getByRole('button', { name: '添加' }).click()
  await page.getByText('wang_support').waitFor()
  await page.getByPlaceholder('姓名').fill('重复')
  await page.getByPlaceholder('平台账号').fill('wang_support')
  await page.getByRole('button', { name: '添加' }).click()
  await page.getByText('该账号已存在').waitFor()
  check(true, '添加我方人员 + 重复校验')

  // 刷新保持登录、深链接
  await page.goto(BASE + '/groups')
  await page.getByText('客户A群').waitFor()
  check(true, '刷新/深链接后保持登录')

  // 手机端
  const phone = await browser.newPage({ viewport: { width: 375, height: 740 } })
  await phone.addInitScript((t) => localStorage.setItem('platformlink.token', t), TOKEN)
  for (const p of ['/presend', '/incidents', '/groups', '/staff']) {
    await phone.goto(BASE + p)
    await phone.waitForTimeout(600)
    const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    check(overflow <= 0, `手机端 ${p} 无横向溢出 (${overflow}px)`)
  }
  if (SHOTS) await phone.screenshot({ path: `${SHOTS}/phone.png` })

  // 退出
  await page.getByRole('button', { name: '退出' }).click()
  await page.getByRole('heading', { name: 'PlatformLink 登录' }).waitFor()
  check(true, '退出登录')

  check(consoleErrors.length === 0, `浏览器无 JS 错误 ${consoleErrors.join(' | ')}`)
  await browser.close()
  console.log(failures ? `\n${failures} 项失败` : '\n全部通过')
  process.exit(failures ? 1 : 0)
})().catch((e) => { console.error('✗ 异常:', e.message); process.exit(1) })
