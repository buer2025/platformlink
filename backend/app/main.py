import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, public
from app.config import settings
from app.db import SessionLocal, run_migrations
from app.services.presend import flush_due, recover_stuck, remind_suppliers

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("platformlink")
STATIC = Path(__file__).resolve().parent.parent / "static"


async def scheduler() -> None:
    while True:
        try:
            async with SessionLocal() as s:
                await remind_suppliers(s)
                await flush_due(s)
        except Exception:
            log.exception("presend scheduler failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.admin_token:
        log.warning("ADMIN_TOKEN 未配置，后台接口将拒绝所有请求")
    await asyncio.to_thread(run_migrations)
    async with SessionLocal() as s:
        await recover_stuck(s)
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="PlatformLink", lifespan=lifespan)
app.include_router(public)
app.include_router(admin)


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
