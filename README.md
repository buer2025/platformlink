# PlatformLink

把 Agent 接进「客户群 ↔ 我方 ↔ 供应商群」：识别客户问题 → 排查 → 回复客户或转交供应商，所有对外消息走预发队列（准确率评分 + 倒计时自动发送）。产品设计见 [docs/PRODUCT_DESIGN.md](docs/PRODUCT_DESIGN.md)。

## 技术栈

- 后端：Python 3.13 + FastAPI + SQLAlchemy 2（async）+ Claude API，`uv` 管理依赖
- 前端：React 19 + TypeScript + Vite + Tailwind CSS v4 + TanStack Query
- 数据库：开发用 SQLite，生产用 PostgreSQL

## 部署（推荐）

```bash
cp backend/.env.example backend/.env   # 填写 ADMIN_TOKEN、各平台密钥、ANTHROPIC_API_KEY
docker compose up -d --build
```

访问 http://服务器:8000 ，用 `ADMIN_TOKEN` 登录。数据库迁移在启动时自动执行。需要在前面加 HTTPS 反向代理（Telegram / 飞书回调要求 HTTPS）。

> 预发调度器运行在应用进程内，请保持**单实例单进程**运行。

## 本地开发

```bash
cd backend && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload
```

```bash
cd frontend && pnpm install && pnpm dev
```

打开 http://localhost:5173 ，用 `.env` 里的 `ADMIN_TOKEN` 登录。未配置 `ANTHROPIC_API_KEY` 时 Agent 走规则兜底（置信度低，基本都需人工确认）。

模拟一条群消息：

```bash
curl -XPOST localhost:8000/api/simulate -H "Authorization: Bearer $ADMIN_TOKEN" -H 'content-type: application/json' -d '{"chat_id":"c1","chat_name":"客户A","text":"seedance 一直 429"}'
```

## IM 接入

| 平台 | 回调地址 | 配置 |
|---|---|---|
| Telegram | `POST /api/webhooks/telegram` | BotFather 关闭 privacy mode；`setWebhook?url=...&secret_token=<TELEGRAM_WEBHOOK_SECRET>` |
| 飞书 | `POST /api/webhooks/feishu` | 事件订阅 `im.message.receive_v1`，填 Verification Token，**不要开启 Encrypt Key**；权限：接收群消息、发送消息 |
| 企业微信 | `POST /api/webhooks/wecom` | 会话存档拉取进程转发，请求头带 `X-Forward-Secret`；**发送尚未实现** |

所有回调都会校验密钥，未配置对应密钥时该平台回调一律拒绝。

## 上线检查清单

1. 设置 `ADMIN_TOKEN`（足够长的随机串）
2. 每个平台配置回调密钥
3. 在「我方人员」登记所有技术支持账号（否则他们在客户群的发言会被当成客户问题）
4. 新群默认「只观察」：逐个在「群与标记」中设为客户群/供应商群、填涉及模型和对接人后再开启自动化
5. 配置 `ANTHROPIC_API_KEY`

## 目录

```
backend/app/
  im/          IM 适配层（telegram / feishu / wecom）
  agent/       分类、排查、风格改写（Claude）；logs.py 日志库查询占位
  services/    pipeline 入站处理；presend 预发队列与调度
  api/         REST 接口
frontend/src/pages/  预发队列、事件中心、群与标记
```

## 测试

```bash
cd backend && uv run pytest                    # 后端单元/集成测试（SQLite）
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest   # 同一套测试跑 PostgreSQL
./e2e/run.sh                                   # 浏览器端到端自检
```

CI（`.github/workflows/ci.yml`）在每次推送时运行以上全部，以及迁移与模型一致性检查。
