"""模型调用留存：每次调用 Claude / Jev 都记一行（输入、输出、耗时、用量、所属事件）。

- 归属：用 contextvars 携带当前的群 / 事件 / 触发消息，调用方不需要层层传参。
- 写入：先进内存队列，由后台任务批量落库。调用路径上不碰数据库，不会因为
  SQLite 写锁或慢查询拖慢回复；写失败会重试，不丢记录。
- 调用开始就写一行 running，结束时更新，前端可以实时看到「正在调用什么」。
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from app.db import SessionLocal
from app.models import ModelCall

log = logging.getLogger(__name__)

MAX_FIELD = 200_000  # 单个字段最多留存的字符数（超长工具结果会被截断）

_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("trace_ctx", default=None)
_ops: list[tuple[str, str, dict]] = []
_wake = asyncio.Event()
_writer: asyncio.Task | None = None
# 本进程里正在进行的调用。落库是异步批量的（SQLite 写锁被占时会延后），
# 进度接口直接读这里，保证「正在调用什么」是实时的。
RUNNING: dict[str, dict] = {}


def bind(**kw: int | None) -> None:
    """记下当前处理的上下文（group_id / incident_id / message_id），之后的模型调用都归到这里。"""
    cur = dict(_ctx.get() or {})
    cur.update({k: v for k, v in kw.items() if v is not None})
    _ctx.set(cur)


class scope:
    """开启一段独立的归属上下文（一条消息的处理、一次定时任务），退出后恢复。同步 / 异步 with 均可。"""

    def __init__(self, **kw: int | None):
        self.kw = {k: v for k, v in kw.items() if v is not None}

    def __enter__(self) -> None:
        self.token = _ctx.set(dict(self.kw))

    def __exit__(self, *exc: object) -> None:
        _ctx.reset(self.token)

    async def __aenter__(self) -> None:
        self.__enter__()

    async def __aexit__(self, *exc: object) -> None:
        self.__exit__()


def context() -> dict:
    return dict(_ctx.get() or {})


def _kind() -> tuple[str, int]:
    """从调用栈找出是哪个业务函数发起的调用（classify / followup / agent …）。"""
    f = sys._getframe(2)
    while f is not None:
        mod, name = f.f_globals.get("__name__", ""), f.f_code.co_name
        if mod == "app.agent.harness" and name == "run":
            return "agent", f.f_locals.get("step", 0) + 1
        if mod in ("app.agent.llm", "app.agent.jev") and not name.startswith("_") and name != "decide":
            return name, 0
        f = f.f_back
    return "other", 0


def _clip(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_FIELD else s[:MAX_FIELD] + "…（已截断）"


def _strip_images(obj: Any) -> Any:
    """截图的 base64 不入库，只记大小。"""
    if isinstance(obj, dict):
        if obj.get("type") == "base64" and isinstance(obj.get("data"), str):
            return {**obj, "data": f"<图片 {len(obj['data']) * 3 // 4} 字节>"}
        return {k: _strip_images(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_images(x) for x in obj]
    return obj


class Call:
    def __init__(self, provider: str, model: str, request: Any):
        self.id = uuid.uuid4().hex
        self.t0 = time.monotonic()
        kind, step = _kind()
        c = context()
        RUNNING[self.id] = {"id": self.id, "kind": kind, "step": step, "provider": provider,
                            "model": model or "", "started_at": datetime.now(UTC),
                            "incident_id": c.get("incident_id"), "message_id": c.get("message_id")}
        _push("insert", self.id, {
            "id": self.id, "created_at": datetime.now(UTC), "provider": provider, "model": model or "",
            "kind": kind, "step": step, "status": "running", "request": _clip(_strip_images(request)),
            "incident_id": c.get("incident_id"), "group_id": c.get("group_id"),
            "message_id": c.get("message_id")})

    def done(self, response: Any = "", input_tokens: int = 0, output_tokens: int = 0,
             error: str = "") -> None:
        RUNNING.pop(self.id, None)
        _push("update", self.id, {
            "status": "error" if error else "ok", "error": error[:4000], "response": _clip(response),
            "input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0,
            "latency_ms": int((time.monotonic() - self.t0) * 1000), "finished_at": datetime.now(UTC)})


def _push(op: str, call_id: str, data: dict) -> None:
    global _writer
    _ops.append((op, call_id, data))
    _wake.set()
    try:
        if _writer is None or _writer.done():
            _writer = asyncio.get_running_loop().create_task(_write_forever())
    except RuntimeError:  # 没有事件循环（不应发生），下次再写
        pass


_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    """写入锁（按事件循环区分）：后台写入任务和 flush() 不能同时写同一批，否则会重复落库。"""
    loop = asyncio.get_running_loop()
    if (lk := _locks.get(id(loop))) is None:
        lk = _locks[id(loop)] = asyncio.Lock()
    return lk


async def _write_batch() -> None:
    async with _lock():
        await _write_batch_unlocked()


async def _write_batch_unlocked() -> None:
    batch = _ops[:500]
    if not batch:
        return
    try:
        async with SessionLocal() as s:
            for op, call_id, data in batch:
                if op == "insert":
                    s.add(ModelCall(**data))
                else:
                    await s.flush()
                    await s.execute(update(ModelCall).where(ModelCall.id == call_id).values(**data))
            await s.commit()
    except Exception as e:  # noqa: BLE001 — 数据库忙等，稍后重试，记录留在队列里
        log.debug("model call log write deferred: %s", e)
        await asyncio.sleep(0.5)
        return
    del _ops[: len(batch)]


async def _write_forever() -> None:
    from app.services import audit

    audit._suppress.set(True)  # 留存写入自己的 SQL 不计入审计
    while True:
        if not _ops:
            _wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_wake.wait(), timeout=5)
            continue
        await asyncio.sleep(0.05)  # 攒一小批
        await _write_batch()


async def flush(timeout: float = 5) -> None:
    """等待队列写完（测试与关停时用）。"""
    deadline = time.monotonic() + timeout
    while _ops and time.monotonic() < deadline:
        await _write_batch()


# ---------- 包装 Anthropic 客户端：所有 messages.parse / create 自动留存 ----------

def _usage(resp: Any) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    if u is None:
        return 0, 0
    cached = (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    return (u.input_tokens or 0) + cached, u.output_tokens or 0


def _output(resp: Any) -> Any:
    parsed = getattr(resp, "parsed_output", None)
    if parsed is not None and hasattr(parsed, "model_dump"):
        return parsed.model_dump()
    return [b.model_dump() for b in getattr(resp, "content", [])]


class TracedMessages:
    def __init__(self, inner: Any):
        self._inner = inner

    async def _run(self, fn: Any, kw: dict) -> Any:
        req = {k: v for k, v in kw.items() if k not in ("output_format",)}
        if fmt := kw.get("output_format"):
            req["output_format"] = getattr(fmt, "__name__", str(fmt))
        call = Call("claude", kw.get("model", ""), req)
        try:
            resp = await fn(**kw)
        except Exception as e:
            call.done(error=f"{type(e).__name__}: {e}")
            raise
        inp, out = _usage(resp)
        call.done(_output(resp), inp, out,
                  error="模型拒绝回答" if getattr(resp, "stop_reason", "") == "refusal" else "")
        return resp

    async def parse(self, **kw: Any) -> Any:
        return await self._run(self._inner.parse, kw)

    async def create(self, **kw: Any) -> Any:
        return await self._run(self._inner.create, kw)


class TracedClient:
    def __init__(self, inner: Any):
        self._inner = inner
        self.messages = TracedMessages(inner.messages)
