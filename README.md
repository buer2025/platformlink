# PlatformLink

把 Agent 接进「客户群 ↔ 我方 ↔ 供应商群」：识别客户问题 → 排查 → 回复客户或转交供应商，所有对外消息走预发队列（准确率评分 + 倒计时自动发送）。产品设计见 [docs/PRODUCT_DESIGN.md](docs/PRODUCT_DESIGN.md)。

## 技术栈

- 后端：Python 3.13 + FastAPI + SQLAlchemy 2（async）+ Claude API，`uv` 管理依赖
- 前端：React 19 + TypeScript + Vite + Tailwind CSS v4 + TanStack Query
- 数据库：开发用 SQLite，生产用 PostgreSQL

## 本地运行

```bash
cd backend && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload
```

```bash
cd frontend && pnpm install && pnpm dev
```

打开 http://localhost:5173 。未配置 `ANTHROPIC_API_KEY` 时 Agent 走规则兜底。

模拟一条群消息：

```bash
curl -XPOST localhost:8000/api/simulate -H 'content-type: application/json' -d '{"chat_id":"c1","chat_name":"客户A","text":"seedance 一直 429"}'
```

## IM 接入

| 平台 | 回调地址 | 说明 |
|---|---|---|
| Telegram | `POST /api/webhooks/telegram` | `setWebhook` 指向该地址；BotFather 关闭 privacy mode |
| 飞书 | `POST /api/webhooks/feishu` | 订阅 `im.message.receive_v1` |
| 企业微信 | `POST /api/webhooks/wecom` | 会话内容存档拉取进程转发到此；发送待实现 |

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
cd backend && uv run pytest
```
