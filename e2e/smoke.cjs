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
  await page.getByRole('button', { name: /使用管理口令登录/ }).click()
  await page.getByPlaceholder('服务端 ADMIN_TOKEN').fill('wrong')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByText('口令错误').waitFor()
  check(true, '错误口令提示')
  await page.getByPlaceholder('服务端 ADMIN_TOKEN').fill(TOKEN)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('heading', { name: /待处理/ }).waitFor()
  check(true, '登录进入预发队列')

  // 预发队列
  const cards = page.getByTestId('outbound')
  await cards.first().waitFor()
  const n = await cards.count()
  check(n >= 3, `预发队列显示 ${n} 条（安抚/客户回复/供应商转交）`)
  check(await page.getByText('转交给').first().isVisible(), '供应商转交单进入队列')
  check(await page.getByText('@yy').first().isVisible(), '转交单 @ 供应商对接人')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/presend.png` })

  // 修改一条
  const reply = cards.filter({ hasText: '回复客户' }).first()
  await reply.getByRole('button', { name: '修改' }).click()
  await reply.locator('textarea').fill('人工修改后的回复')
  await reply.getByRole('button', { name: /保存/ }).click()
  await page.getByText('人工修改后的回复').waitFor()
  check(true, '修改消息后保存')
  // 取消一条
  const ticket = cards.filter({ hasText: '转交给' }).first()
  await ticket.getByRole('button', { name: '取消' }).click()
  await page.getByText('已取消').first().waitFor()
  await page.getByRole('button', { name: '历史' }).click()
  await page.getByRole('heading', { name: '历史' }).waitFor()
  await cards.filter({ hasText: '已取消' }).first().waitFor()
  check(true, '取消消息进入历史')
  await page.getByRole('button', { name: /待处理/ }).click()
  // 键盘：J 选中第一条，E 进入编辑，Esc 退出
  await page.keyboard.press('j')
  await page.keyboard.press('e')
  await page.locator('[data-testid=outbound] textarea').first().waitFor()
  await page.keyboard.press('Escape')
  check(true, '键盘 J / E 操作队列')
  // 立即发送（未配置 bot token，应显示失败与重新发送）
  await cards.filter({ hasText: '人工修改后的回复' }).getByRole('button', { name: '立即发送' }).click()
  await page.getByRole('button', { name: '重新发送' }).first().waitFor({ timeout: 20000 })
  check(true, '发送失败时显示原因并可重新发送')

  // 事件中心
  await page.getByRole('link', { name: '事件中心' }).click()
  // 等页面切换动画结束（旧页面卸载）再点击，避免点到正在退出的预发队列
  await page.getByRole('heading', { name: '事件中心' }).waitFor()
  const row = page.locator('button[data-row]').filter({ hasText: '429 限流' }).first()
  await row.waitFor()
  await row.click()
  await page.getByText('req-e2e-0001', { exact: true }).waitFor()
  check(true, '事件详情显示 request_id')
  await page.getByText('日志预查询').waitFor()
  check(await page.getByText('Agent 结论').count() >= 1, '处理过程时间线（分诊 / 日志 / 结论）')
  await page.getByPlaceholder('内部备注（客户看不到），记入处理过程…').fill('已电话联系客户')
  await page.getByRole('button', { name: '记录' }).click()
  await page.getByText('已电话联系客户').waitFor()
  check(true, '添加备注写入时间线')
  // 半 AI 半人工：从事件详情以机器人身份发消息（未配置 bot，应提示发送失败原因）
  await page.getByPlaceholder(/以机器人身份发到/).fill('我是小王，这个我来跟进')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await page.getByText(/发送失败/).first().waitFor()
  check(true, '后台人工发送（失败时提示原因）')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/incidents.png` })
  await page.getByRole('button', { name: '标记已解决' }).click()
  await page.getByText('暂无事件').waitFor()
  check(true, '标记已解决后从未解决列表消失')
  await page.getByRole('button', { name: '沉淀为知识' }).click()
  await page.getByText(/从事件 #\d+ 沉淀知识/).waitFor()
  await page.getByRole('dialog').last().getByRole('button', { name: '保存' }).click()
  await page.getByText(/已保存知识 #/).waitFor()
  check(true, '已解决事件一键沉淀为知识')
  await page.keyboard.press('Escape')

  // 链路与知识库
  await page.getByRole('link', { name: '处理链路' }).click()
  await page.getByText('模型用量需求').waitFor()
  check(await page.getByText('模型调用问题').isVisible(), '默认链路已创建')
  await page.keyboard.press('c')
  await page.getByPlaceholder('例如：模型用量需求').fill('海外客户专线')
  await page.getByRole('button', { name: '用量需求' }).click()
  await page.keyboard.press('Control+Enter')
  await page.getByText('海外客户专线').first().waitFor()
  check(true, '快捷键 C 新建链路，⌘↵ 保存')
  await page.getByRole('link', { name: '知识库' }).click()
  await page.getByPlaceholder(/粘贴报错原文/).fill('429')
  await page.locator('h3').first().waitFor()
  check(await page.locator('h3').count() >= 1, '知识库检索')
  await page.keyboard.press('Control+k')
  await page.getByPlaceholder('搜索页面、事件、群…').fill('群与标记')
  await page.keyboard.press('Enter')
  await page.getByPlaceholder('搜索群名 / 模型 / 负责人').waitFor()
  check(true, '⌘K 命令面板跳转')

  // 群与标记
  await page.getByRole('link', { name: '群与标记' }).click()
  await page.getByText('客户A群').click()
  await page.getByText('日志库绑定').waitFor()
  await page.getByPlaceholder('王总 / 哥 / 老师').fill('王总')
  await page.getByPlaceholder('正式 / 专业亲和 / 轻松 / 熟络 / 英文').fill('熟络')
  await page.getByPlaceholder('VIP / KA / 主力供应商').fill('VIP')
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/groups.png`, fullPage: true })
  await page.getByRole('button', { name: '保存' }).click()
  await page.locator('span', { hasText: /^VIP$/ }).first().waitFor()
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

  // 数据看板
  await page.getByRole('link', { name: '数据看板' }).click()
  await page.getByText('AI 原稿采用率').waitFor()
  check(await page.getByText('每天新事件').isVisible(), '数据看板')

  // 账号：管理员建账号 → 退出 → 个人账号登录 → 值班 → 认领事件
  await page.goto(BASE + '/users')
  await page.getByPlaceholder('小王').fill('小张')
  await page.getByPlaceholder('wang').fill('zhang')
  await page.locator('input[type=password]').fill('password123')
  await page.getByRole('button', { name: '添加' }).click()
  await page.getByText('zhang', { exact: true }).waitFor()
  check(true, '管理员创建个人账号')
  await page.getByRole('button', { name: '退出' }).click()
  await page.getByRole('heading', { name: 'PlatformLink 登录' }).waitFor()
  await page.getByLabel('用户名').fill('zhang')
  await page.getByLabel('密码').fill('password123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.locator('aside').getByText('小张').waitFor()
  check(await page.getByRole('link', { name: '账号与值班' }).count() === 0, '个人账号登录（技术支持看不到账号管理）')
  await page.goto(BASE + '/users')
  await page.getByRole('heading', { name: '预发队列' }).waitFor()
  check(true, '技术支持访问账号管理被拦回')
  await page.locator('aside').getByRole('switch', { name: '值班' }).click()
  await page.getByText('值班中 · 会分到新事件').waitFor()
  check(true, '一键切换值班')
  await page.goto(BASE + '/incidents')
  await page.getByRole('button', { name: '全部' }).click()
  await page.locator('button[data-row]').first().click()
  await page.getByRole('button', { name: '认领' }).click()
  await page.getByText('已指派给 小张').waitFor()
  check(true, '认领事件')
  const steps = page.locator('section ol li')
  await steps.filter({ hasText: '收到消息' }).waitFor()
  check(await steps.filter({ hasText: '解决' }).count() === 1, '事件抽屉显示实时处理进度')
  await page.getByRole('button', { name: /^模型调用 \d+/ }).click()
  await page.getByText(/还没有模型调用|次调用/).waitFor()
  check(true, '事件抽屉可查看模型调用')
  await page.keyboard.press('Escape')
  await page.goto(BASE + '/calls')
  await page.getByRole('heading', { name: '模型调用' }).waitFor()
  await page.getByText('24 小时调用').waitFor()
  check(true, '模型调用页可访问')

  // 刷新保持登录、深链接
  await page.goto(BASE + '/groups')
  await page.getByText('客户A群').waitFor()
  check(true, '刷新/深链接后保持登录')

  // 手机端
  const phone = await browser.newPage({ viewport: { width: 375, height: 740 } })
  await phone.addInitScript((t) => localStorage.setItem('platformlink.token', t), TOKEN)
  for (const p of ['/presend', '/incidents', '/quota', '/workflows', '/knowledge', '/groups', '/staff', '/stats', '/calls']) {
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
