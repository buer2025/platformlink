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

> 支持多进程 / 多副本（如 `uvicorn --workers 4` 或多个容器）：调度器、Telegram 轮询和同群消息处理都通过数据库租约协调。多副本请使用 PostgreSQL；SQLite 只适合单机。

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
| 飞书 | `POST /api/webhooks/feishu` | 事件订阅 `im.message.receive_v1`，填 Verification Token；开启 Encrypt Key 时同时配置 `FEISHU_ENCRYPT_KEY`；权限：接收群消息、发送消息 |
| 企业微信 | `POST /api/webhooks/wecom` | 接收：会话存档拉取进程转发，请求头带 `X-Forward-Secret`；发送：在群设置里填「群机器人 Webhook」（仅内部群，外部客户群请设为需人工确认） |

所有回调都会校验密钥，未配置对应密钥时该平台回调一律拒绝。

## 排查 Agent

客户消息 → 分诊（类别 / 模型 / request_id）→ 匹配**处理链路** → 按链路预查日志库 → **工具调用循环**（参考 Claude Code harness）→ 结论 + 自检 → 预发队列。

- **处理链路**（后台「处理链路」）：管理员定义触发条件（类别、关键词、群标识）、预查询步骤、给 Agent 的处理说明、是否转交供应商、是否强制人工确认。首次启动自动创建「模型调用问题」「模型用量需求」两条。
- **工具**：`query_request_id`、`recent_errors`、`usage_stats`、`channel_status`、`search_knowledge`、`similar_incidents`、`group_history`。Agent 自主决定调用顺序（最多 8 步），每次调用都写进事件时间线。
- **准确率护栏**：没有日志证据也没有命中知识时，结论最高 60 分，不会自动发出；判断不出责任方最高 50 分。
- **知识沉淀**：事件解决后点「沉淀为知识」，Agent 根据处理过程起草经验条目；之后按模型、关键词、现象自动检索，命中次数会累计。

### 日志库（new-api）

```
LOG_DATABASE_URL=mysql+aiomysql://readonly:密码@host:3306/new-api   # 只读账号
```

读取 `logs` 与 `channels` 表（启动后反射表结构，兼容有无 `request_id` 列的版本）。在「群与标记」里：客户群填 new-api **用户名/用户 ID**，供应商群填**渠道 ID**——日志定位到哪个渠道出错，就精确转交到哪个供应商群。群详情里可「测试查询」验证绑定。

## Agent 评估集

改提示词 / 换模型前后各跑一次，看准确率有没有退步（需要 `ANTHROPIC_API_KEY`）：

```bash
cd backend && uv run python -m evals.run
```

覆盖分诊、责任方判断（基于 new-api 模拟日志）、证据不足封顶、不泄露内部信息、跟进时该不该回、风格禁忌与克制。用例在 `backend/evals/cases.py`，遇到判断错的真实案例就补一条进去。

## 键盘操作

`⌘K` 命令面板 · `G` 然后 `P/I/W/K/G/S` 切换页面 · `J/K` 上下 · `↵` 打开 · `⌘↵` 发送/保存 · `E` 修改 · `R` 标记解决 · `C` 新建 · `⇧T` 主题 · `?` 全部快捷键

## 账号与值班

首次部署用 `ADMIN_TOKEN` 登录（登录页「使用管理口令登录」），在「账号与值班」给每位同事建账号。
- 角色：管理员（管理账号、链路、我方人员）/ 技术支持（处理事件、发消息、打标记）
- 值班中的同事参与自动分配：群负责人优先，否则分给手上未结事件最少的人
- 显示名与「我方人员」姓名一致时，升级会 @ 到他的 IM 账号

## 上线检查清单

1. 设置 `ADMIN_TOKEN`（足够长的随机串）
2. 每个平台配置回调密钥
3. 在「我方人员」登记所有技术支持账号（否则他们在客户群的发言会被当成客户问题）
4. 新群默认「只观察」：逐个在「群与标记」中设为客户群/供应商群、填涉及模型和对接人后再开启自动化
5. 配置 `ANTHROPIC_API_KEY`（使用中转时另设 `ANTHROPIC_BASE_URL`）
6. 配置 `LOG_DATABASE_URL`，并给客户群 / 供应商群绑定 new-api 用户与渠道

## 目录

```
backend/app/
  im/          IM 适配层（telegram / feishu / wecom）
  agent/       分诊、排查循环、证据清单、风格改写（Claude + Jev）；logs.py 查询 new-api 日志库
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
