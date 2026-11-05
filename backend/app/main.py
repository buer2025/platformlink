import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.audit import router as audit_router
from app.api.calls import router as calls_router
from app.api.routes import admin, public
from app.config import settings
from app.db import SessionLocal, engine, run_migrations
from app.services import audit, lease, retention, trace
from app.services.memory import close_idle
from app.services.presend import flush_due, recover_stuck, remind_suppliers
from app.services.quota import auto_push
from app.services.seed import seed_defaults
from app.services.sla import check_sla

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("platformlink")
STATIC = Path(__file__).resolve().parent.parent / "static"


async def scheduler() -> None:
    """后台定时任务。多进程 / 多副本时只有拿到 "scheduler" 租约的那个进程执行，
    其他进程空转待命；持有者挂掉后租约过期，下一个进程自动接手。"""
    last_close = 0.0
    last_sla = 0.0
    ttl = max(30.0, settings.scheduler_interval_seconds * 6)
    leader = False
    while True:
        try:
            is_leader = await lease.acquire("scheduler", ttl)
            if is_leader != leader:
                leader = is_leader
                log.info("scheduler %s (%s)", "接管" if leader else "待命", lease.HOLDER)
                audit.system("调度器接管" if leader else "调度器转为待命", lease.HOLDER)
            if not leader:
                await asyncio.sleep(settings.scheduler_interval_seconds)
                continue
            async with SessionLocal() as s, trace.scope():  # 定时任务里的模型调用各自归属
                await _job(s, "恢复卡住的发送", recover_stuck, "{} 条发送中断的消息标为失败")
                await _job(s, "供应商催办", remind_suppliers, "生成 {} 条催办消息")
                await _job(s, "自动发送到期预发消息", flush_due, "发送 {} 条")
                if time.monotonic() - last_close > 300:  # 每 5 分钟检查一次空闲会话
                    last_close = time.monotonic()
                    await _job(s, "自动关闭空闲事件", close_idle, "关闭 {} 个事件")
                    await _job(s, "自动起草催量", auto_push, "起草 {} 条催量消息")
                    await _job(s, "清理过期记录", retention.purge, "删除 {} 条")
                if time.monotonic() - last_sla > 60:  # 每分钟检查一次超时未回复
                    last_sla = time.monotonic()
                    await _job(s, "SLA 超时提醒", check_sla, "提醒 {} 个事件")
        except Exception:
            log.exception("presend scheduler failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


async def _job(s, action: str, fn, summary: str) -> None:
    """跑一个定时任务；做了事（返回数量 > 0）或出错才记审计，空转不记。"""
    async with audit.op("scheduler", action) as o:
        n = await fn(s)
        o.skip = not n
        o.note(summary.format(n))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.admin_token:
        log.warning("ADMIN_TOKEN 未配置，后台接口将拒绝所有请求")
    audit.instrument(engine, "app")
    await asyncio.to_thread(run_migrations)
    async with SessionLocal() as s:
        await seed_defaults(s)
    audit.system("服务启动", lease.HOLDER)
    tasks = [asyncio.create_task(scheduler())]
    if settings.telegram_mode == "polling" and settings.telegram_bot_token:
        from app.im.telegram_poll import poll_forever

        tasks.append(asyncio.create_task(poll_forever()))
    yield
    audit.system("服务停止", lease.HOLDER)
    await trace.flush()
    await audit.flush()
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="PlatformLink", lifespan=lifespan)
app.add_middleware(audit.Middleware)
app.include_router(public)
app.include_router(admin)
app.include_router(calls_router)
app.include_router(audit_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# 生产环境：同一进程托管前端构建产物（frontend/dist 复制到 backend/static）
if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = STATIC / path
        if path and f.is_file() and STATIC in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(STATIC / "index.html")
