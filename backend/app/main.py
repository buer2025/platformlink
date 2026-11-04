import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import SessionLocal, init_db
from app.services.presend import flush_due

log = logging.getLogger("platformlink")


async def scheduler() -> None:
    while True:
        try:
            async with SessionLocal() as s:
                await flush_due(s)
        except Exception:
            log.exception("presend scheduler failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="PlatformLink", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
