# PlatformLink — 给 Claude 的项目说明

IM 群（企业微信 / 飞书 / Telegram）接入 AI Agent：客户群报障 → 分诊 → 查 new-api 日志排查 → 回复客户或转交供应商群 → 催办 → 解决。
所有对外消息都进**预发队列**（准确率评分 + 倒计时自动发送，低分需人工审批）。产品设计见 `docs/PRODUCT_DESIGN.md`，已完成 / 待做事项见 `docs/ROADMAP.md`。

## 常用命令

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload   # 后端开发（:8000）
cd frontend && pnpm install && pnpm dev                          # 前端开发（:5173）
cd backend && uv run ruff check . --exclude migrations           # 后端 lint
cd backend && uv run pytest -q                                   # 测试（SQLite）
TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest -q      # 同一套测试跑 Postgres
cd backend && uv run alembic revision --autogenerate -m "..."    # 改 models.py 后生成迁移
cd backend && uv run python -m evals.run                         # Agent 评估集（需真实模型 key）
cd frontend && pnpm lint && pnpm build                           # 前端检查
./e2e/run.sh                                                     # 浏览器端到端自检（Playwright）
```

完成一项改动前至少跑：ruff、pytest、`pnpm lint && pnpm build`；动了提示词或分诊/跟进逻辑还要跑 evals；动了页面要跑 e2e。CI（`.github/workflows/ci.yml`）会在 SQLite + Postgres 上跑测试并做 `alembic check`。

## 结构

```
backend/app/
  main.py        启动：自动跑迁移；调度器（抢 "scheduler" 租约的进程才执行）
  models.py      全部表；改了必须配迁移（backend/migrations/versions）
  im/            平台适配（telegram / feishu / wecom）；telegram_poll 轮询模式
  agent/
    llm.py       分诊 / 跟进判断 / 改写 / 摘要（Claude 结构化输出，失败走规则兜底）
    jev.py       AGTCloud Jev 决策模型：分诊、跟进先走 Jev，不确定（noul > 0.2）回退 Claude
    harness.py   排查 Agent 的工具调用循环（工具定义、Finding 结论）
    logs.py      new-api 日志库查询（request_id 精确 + 模糊、时间窗、跨客户渠道影响）
    evidence.py  证据清单 + 置信度封顶（calibrate）
  services/
    pipeline.py  入站消息主流程（分诊 → 建/合并事件 → 排查 → 回复/转交）
    presend.py   预发队列：评分 → 倒计时 → 发送；卡住恢复；供应商催办
    lease.py     数据库租约（多进程协调）
    trace.py     模型调用留存（TracedClient、RUNNING 实时表、批量写入）
  api/           routes.py 主要接口；calls.py 模型调用与 /live 实时进度
backend/evals/   cases.py 用例、run.py 评估、jev_compare.py Jev vs Claude 对比
frontend/src/    pages/ 各页面；components/；api.ts 类型与请求；ui.tsx 基础组件
e2e/smoke.cjs    端到端冒烟（含移动端溢出检查）
.claude/skills/  已审查的第三方 skill（见其 README）
```

## 必须遵守的约定

- **密钥只放 `backend/.env`**（已 gitignore）。不要提交、不要写进日志、测试、文档或提交信息。`.env.example` 只写变量名和说明。
- **多进程安全**：调度器、Telegram 轮询、同群消息处理都靠 `leases` 表协调。新增后台任务或"同一时间只能一个人做"的逻辑时，用 `lease.acquire` / `lease.hold`，不要假设单进程。同群处理顺序是先进程内 `asyncio.Lock`（保证 FIFO）再拿数据库租约。
- **对外消息一律走 `presend.enqueue`**，不要直接调用 adapter 发送。评分决定是否自动发出；低风险用途（ack / greeting / 内部群 escalation）才立即发。
- **结构化输出字段要设为必填**：带默认值的字段模型常会直接略过（`reply_kind`、`Finding.self_check` 都踩过）。新增字段时设必填，并在提示词里说明怎么填。
- **AI 不可用时必须有规则兜底**，兜底结果置信度低、走人工审批。测试默认不连真实模型（conftest 把 `JEV_MODEL` 设为空）。
- **模型调用都要能追踪**：新建 Anthropic 客户端用 `llm._client()`（已包 `TracedClient`）；入口处用 `trace.scope()` / `trace.bind(...)` 绑定 group / incident / message。
- **实时进度依赖及时提交**：长流程（排查循环）在每次工具调用后、`harness.run` 前提交，否则 SQLite 写锁会让时间线和进度看起来卡住。
- **置信度不能虚高**：证据有矛盾 → 最高 70；没有强证据 → 60；只有 1 条强证据 → 80。改排查逻辑时保持这套封顶，并补测试（`backend/tests/test_evidence.py`）。
- **时间**：库里存 UTC（读出来可能是 naive，用 `presend.aware()`）；展示给人看的时间按 `settings.timezone`（Asia/Shanghai）。
- **改了 models.py 必须生成迁移**，并在 SQLite 和 Postgres 都能跑通；CI 会做 `alembic check`。
- **前端**：用 `ui.tsx` 里的组件和 Tailwind token，不要硬编码颜色；要兼容深色模式和手机宽度（e2e 会检查横向溢出）；用户可见文案用中文。
- **评估集**：遇到模型判断错的真实案例，先在 `backend/evals/cases.py` 补一条用例，再改提示词。

## 服务器部署与运维

生产在自有服务器上跑，不在云端开发容器里。

```bash
cp backend/.env.example backend/.env    # 填 ADMIN_TOKEN、各平台密钥、模型 key、LOG_DATABASE_URL
docker compose up -d --build            # Postgres + 应用，迁移在启动时自动执行
docker compose logs -f app              # 看日志
git pull && docker compose up -d --build   # 升级（迁移自动执行，升级前先备份）
docker compose exec db pg_dump -U platformlink platformlink > backup-$(date +%F).sql   # 备份
```

- 前面要加 HTTPS 反向代理（Telegram / 飞书回调要求 HTTPS），应用以 `--proxy-headers` 启动。没有公网地址时 Telegram 用 `TELEGRAM_MODE=polling`。
- 生产用 PostgreSQL；可以开多 worker 或多副本（租约协调），SQLite 只适合单进程开发。
- `/healthz` 是健康检查。
- `model_calls` 表会持续增长（每次模型调用一行，含请求/响应）：需要定期清理或归档。
- new-api 日志库用**只读账号**；`NEWAPI_ADMIN_TOKEN` 的渠道实测每次会向上游发一次真实请求。
- 运维排查改动时，先在测试库或备份上验证迁移，再升级生产。

## 相关文档

- `README.md`：部署、IM 接入、上线检查清单
- `docs/ROADMAP.md`：已完成和待做的功能
- `docs/PRODUCT_DESIGN.md`：产品设计
