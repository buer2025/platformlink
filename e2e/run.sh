#!/usr/bin/env bash
# 一键端到端自检：构建前端 → 后端托管 → 全新数据库 → 浏览器跑完整流程
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
(cd "$ROOT/frontend" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
rm -rf "$ROOT/backend/static" && cp -r "$ROOT/frontend/dist" "$ROOT/backend/static"
cd "$ROOT/backend"
# 显式清空外部依赖，避免读到本地 .env 里的真实 Key / Bot / 日志库
DATABASE_URL="sqlite+aiosqlite:///$TMP/e2e.db" ADMIN_TOKEN=e2e-token SCHEDULER_INTERVAL_SECONDS=1 \
  ANTHROPIC_API_KEY= ANTHROPIC_BASE_URL= TELEGRAM_BOT_TOKEN= TELEGRAM_MODE=webhook LOG_DATABASE_URL= \
  uv run uvicorn app.main:app --port 8765 >"$TMP/server.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 30); do curl -sf localhost:8765/healthz >/dev/null && break; sleep 1; done
node "$ROOT/e2e/smoke.cjs" http://localhost:8765 e2e-token
