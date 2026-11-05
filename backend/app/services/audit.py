"""操作审计与数据库查询耗时。

记什么：
- 后台接口：所有写操作（新增 / 修改 / 删除 / 发送…）、登录登出、权限拒绝、出错的请求；
  `AUDIT_READS=true` 时连只读请求也记。谁、什么时候、从哪个 IP、做了什么、参数（密码密钥打码）、
  结果、耗时、其间执行了多少条 SQL、花了多少数据库时间。
- 平台回调（webhook）、Agent 处理每条群消息、定时任务（自动发送、催办、SLA、自动关闭…）、
  服务启动 / 停止 / 调度器接管，都按同样格式记一行，操作者分别是平台、Agent、系统。
- 数据库查询：new-api 日志库的每一次查询都单独记一行（语句、参数、耗时、行数、哪个函数发起的）；
  业务库只单独记超过 `SLOW_QUERY_MS` 的慢查询，其余按操作汇总进审计行的 db_count / db_ms。

写入方式和模型调用留存一样：先进内存队列、后台批量落库，请求路径上不额外碰数据库。
写审计本身产生的 SQL 不计入、不记录，避免自我放大。
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import re
import sys
import time
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.config import settings

log = logging.getLogger(__name__)

MAX_REQUEST = 20_000
SECRET_KEYS = ("password", "token", "secret", "api_key", "apikey", "authorization", "cookie")


@dataclass
class Op:
    """一次正在进行的操作（一个请求、一次 Agent 处理、一次定时任务）。"""

    source: str
    action: str
    actor: str = ""
    actor_id: int | None = None
    role: str = ""
    target: str = ""
    detail: list[str] = field(default_factory=list)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    t0: float = field(default_factory=time.monotonic)
    db_count: int = 0
    db_ms: float = 0.0
    ok: bool = True
    skip: bool = False  # 定时任务这一轮什么都没做时不记，避免每 5 秒一行空记录
    incident_id: int | None = None
    group_id: int | None = None

    def note(self, text: str) -> None:
        self.detail.append(text)


_cur: contextvars.ContextVar[Op | None] = contextvars.ContextVar("audit_op", default=None)
_suppress: contextvars.ContextVar[bool] = contextvars.ContextVar("audit_suppress", default=False)


def current() -> Op | None:
    return _cur.get()


def note(text: str) -> None:
    """给当前操作追加一条说明（例如自动发送了哪条消息）。"""
    if (o := _cur.get()) is not None:
        o.note(text)


def set_actor(actor_id: int | None, name: str, role: str) -> None:
    """鉴权通过后调用：把当前请求的操作者记到审计行上。"""
    if (o := _cur.get()) is not None:
        o.actor, o.actor_id, o.role = name, actor_id, str(getattr(role, "value", role))


def _ids(o: Op) -> tuple[int | None, int | None]:
    from app.services import trace

    c = trace.context()
    return o.incident_id or c.get("incident_id"), o.group_id or c.get("group_id")


def _row(o: Op, **extra: Any) -> dict:
    incident_id, group_id = _ids(o)
    return {
        "created_at": datetime.now(UTC), "trace_id": o.trace_id, "source": o.source,
        "actor": o.actor[:100], "actor_id": o.actor_id, "role": o.role[:20], "action": o.action[:100],
        "target": o.target[:100], "ok": o.ok, "latency_ms": int((time.monotonic() - o.t0) * 1000),
        "db_count": o.db_count, "db_ms": int(o.db_ms), "detail": "\n".join(o.detail)[:MAX_REQUEST],
        "incident_id": incident_id, "group_id": group_id, **extra,
    }


class op:
    """记录一段非 HTTP 的操作：`async with audit.op("scheduler", "发送到期预发消息") as o: ...`。

    出异常时记为失败（异常照常抛出）；`o.skip = True` 表示这次不用记。嵌套时内层的数据库
    统计会加回外层。
    """

    def __init__(self, source: str, action: str, actor: str = "系统", target: str = "", detail: str = "",
                 incident_id: int | None = None, group_id: int | None = None):
        self.o = Op(source=source, action=action, actor=actor, target=target,
                    detail=[detail] if detail else [], incident_id=incident_id, group_id=group_id)

    async def __aenter__(self) -> Op:
        self.parent = _cur.get()
        self.token = _cur.set(self.o)
        return self.o

    async def __aexit__(self, typ: object, exc: BaseException | None, tb: object) -> None:
        o = self.o
        _cur.reset(self.token)
        if self.parent is not None:
            self.parent.db_count += o.db_count
            self.parent.db_ms += o.db_ms
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            o.ok = False
            o.note(f"出错：{type(exc).__name__}: {exc}"[:2000])
        elif o.skip:
            return
        push("audit", _row(o))


def system(action: str, detail: str = "", ok: bool = True) -> None:
    """记一条即时的系统事件（服务启动、调度器接管…）。"""
    o = Op(source="system", action=action, actor="系统", detail=[detail] if detail else [], ok=ok)
    push("audit", _row(o))


# ---------- 打码 ----------

def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if any(s in str(k).lower() for s in SECRET_KEYS) and v else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def _body_text(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        s = json.dumps(redact(json.loads(raw)), ensure_ascii=False)
    except ValueError:
        s = f"<{len(raw)} 字节非 JSON 内容>"
    return s if len(s) <= MAX_REQUEST else s[:MAX_REQUEST] + "…（已截断）"


# ---------- 数据库查询计时 ----------

_SECRET_COLS = re.compile(r"password|token_hash|secret|api_key", re.IGNORECASE)
_PLUMBING = {"rows", "table", "<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>"}  # LogDB 的通用查询方法


def _caller() -> str:
    """找出发起这条 SQL 的业务函数。SQLAlchemy 异步模式下 SQL 在子 greenlet 里执行，
    业务代码的栈帧在父 greenlet 上，所以两段都要看。"""
    frames = [sys._getframe(2)]
    with contextlib.suppress(Exception):
        import greenlet

        g = greenlet.getcurrent().parent
        while g is not None:
            if g.gr_frame is not None:
                frames.append(g.gr_frame)
            g = g.parent
    for f in frames:
        while f is not None:
            mod = f.f_globals.get("__name__", "")
            if (mod.startswith("app.") and mod not in ("app.services.audit", "app.db")
                    and f.f_code.co_name not in _PLUMBING):
                return f"{mod.rsplit('.', 1)[-1]}.{f.f_code.co_name}"
            f = f.f_back
    return ""


def _params(statement: str, parameters: Any) -> str:
    if _SECRET_COLS.search(statement):  # 密码哈希、会话令牌这类参数不落库
        return "***"
    s = repr(parameters)
    return s if len(s) <= 2000 else s[:2000] + "…"


_instrumented: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def instrument(engine: Any, db: str) -> None:
    """给引擎挂上计时。db="newapi" 每条都记；db="app" 只记慢查询，其余汇总到当前操作。"""
    sync: Engine = getattr(engine, "sync_engine", engine)
    if sync in _instrumented:
        return
    _instrumented[sync] = db

    @event.listens_for(sync, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("pl_t0", []).append(time.perf_counter())

    def _done(conn, statement: str, parameters: Any, rows: int, error: str) -> None:
        stack = conn.info.get("pl_t0") or [time.perf_counter()]
        ms = (time.perf_counter() - stack.pop()) * 1000
        if _suppress.get():
            return
        if (o := _cur.get()) is not None:
            o.db_count += 1
            o.db_ms += ms
        if db != "newapi" and ms < settings.slow_query_ms and not error:
            return
        from app.services import trace

        c = trace.context()
        push("query", {
            "created_at": datetime.now(UTC), "trace_id": o.trace_id if o else "", "db": db,
            "op": (statement.lstrip().split(None, 1) or [""])[0].lower()[:10], "caller": _caller()[:100],
            "statement": statement[:4000], "params": _params(statement, parameters),
            "duration_ms": round(ms, 2), "rows": rows, "error": error[:2000],
            "incident_id": c.get("incident_id"), "group_id": c.get("group_id"),
        })

    @event.listens_for(sync, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        rc = getattr(cursor, "rowcount", -1)
        _done(conn, statement, parameters, rc if isinstance(rc, int) else -1, "")

    @event.listens_for(sync, "handle_error")
    def _error(ctx):
        if ctx.connection is not None and ctx.statement:
            _done(ctx.connection, ctx.statement, ctx.parameters,
                  -1, f"{type(ctx.original_exception).__name__}: {ctx.original_exception}")


# ---------- 批量写入 ----------

_queue: list[tuple[str, dict]] = []
_wake = asyncio.Event()
_writer: asyncio.Task | None = None


def push(kind: str, data: dict) -> None:
    global _writer
    _queue.append((kind, data))
    _wake.set()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 不在事件循环里（同步代码路径），等下一次 push 或 flush 再写
        return
    if _writer is None or _writer.done() or _writer.get_loop() is not loop:
        # 写入任务不能继承当前操作的上下文，否则它自己的 SQL 会被计进去
        _writer = loop.create_task(_write_forever(), context=contextvars.Context())


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
    from app.db import SessionLocal
    from app.models import AuditLog, DbQuery

    batch = _queue[:500]
    if not batch:
        return
    token = _suppress.set(True)
    try:
        async with SessionLocal() as s:
            for kind, data in batch:
                s.add(AuditLog(**data) if kind == "audit" else DbQuery(**data))
            await s.commit()
    except Exception as e:  # noqa: BLE001 — 数据库忙，稍后重试，记录留在队列里
        log.debug("audit write deferred: %s", e)
        await asyncio.sleep(0.5)
        return
    finally:
        _suppress.reset(token)
    del _queue[: len(batch)]


async def _write_forever() -> None:
    _suppress.set(True)
    while True:
        if not _queue:
            _wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_wake.wait(), timeout=5)
            continue
        await asyncio.sleep(0.05)
        await _write_batch()


async def flush(timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while _queue and time.monotonic() < deadline:
        await _write_batch()


# ---------- HTTP 中间件 ----------

ACTIONS = {
    "login": "登录", "logout": "退出登录", "update_me": "修改个人设置",
    "create_user": "新建账号", "update_user": "修改账号",
    "update_group": "修改群设置", "learn_group_style": "学习群风格", "preview_group_style": "预览群风格",
    "delete_memory": "删除群记忆", "manual_send": "手动发送消息", "ai_draft": "AI 起草",
    "quota_push": "催量", "create_party": "新建合作方", "create_staff": "登记我方人员",
    "delete_staff": "删除我方人员", "update_incident": "修改事件",
    "send_now": "立即发送预发消息", "edit": "修改预发消息", "cancel": "取消预发消息",
    "create_workflow": "新建处理链路", "update_workflow": "修改处理链路", "delete_workflow": "删除处理链路",
    "lookup_test": "测试日志查询", "create_knowledge": "新建知识", "update_knowledge": "修改知识",
    "knowledge_draft": "起草知识", "simulate": "模拟群消息", "webhook": "平台回调",
}
READ = {"GET", "HEAD", "OPTIONS"}


class Middleware:
    """纯 ASGI 中间件：不缓冲响应、不影响流式返回；只在 /api/ 下工作。"""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        o = Op(source="http", action="")
        token = _cur.set(o)
        body = bytearray()
        status = 0
        done = False

        async def recv() -> dict:
            msg = await receive()
            if msg["type"] == "http.request" and len(body) < MAX_REQUEST * 2:
                body.extend(msg.get("body", b""))
            return msg

        async def snd(msg: dict) -> None:
            nonlocal status, done
            if msg["type"] == "http.response.start":
                status = msg["status"]
            await send(msg)
            # 响应发完就记（webhook 的后台处理在之后进行，单独记成 Agent 操作）
            if msg["type"] == "http.response.body" and not msg.get("more_body") and not done:
                done = True
                self._record(scope, o, status, bytes(body))

        try:
            await self.app(scope, recv, snd)
        except Exception:
            if not done:
                done = True
                self._record(scope, o, 500, bytes(body))
            raise
        finally:
            _cur.reset(token)

    @staticmethod
    def _record(scope: dict, o: Op, status: int, body: bytes) -> None:
        method = scope["method"]
        route = scope.get("route")
        name = getattr(getattr(route, "endpoint", None), "__name__", "")
        if method in READ and status < 400 and not settings.audit_reads:
            return
        if method == "OPTIONS":
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        params = scope.get("path_params") or {}
        o.ok = status < 400
        o.action = ACTIONS.get(name) or (f"查看 {getattr(route, 'path', scope['path'])}" if method in READ
                                         else f"{method} {getattr(route, 'path', scope['path'])}")
        if name == "webhook":
            o.source, o.actor = "webhook", str(params.get("platform", ""))
            request = f"<{len(body)} 字节，原文见群消息记录>"
        else:
            request = _body_text(body)
            if name == "login" and not o.actor:
                with contextlib.suppress(Exception):
                    o.actor = f"{json.loads(body).get('username', '')}（尝试）"
        if status in (401, 403) and not o.actor:
            o.actor = "未登录"
        if ids := {k: v for k, v in params.items() if k.endswith("_id") or k == "kid"}:
            o.target = " ".join(f"{'knowledge' if k == 'kid' else k.removesuffix('_id')}#{v}"
                                for k, v in ids.items())
            if "incident_id" in ids:
                with contextlib.suppress(ValueError, TypeError):
                    o.incident_id = int(ids["incident_id"])
            if "group_id" in ids:
                with contextlib.suppress(ValueError, TypeError):
                    o.group_id = int(ids["group_id"])
        client = scope.get("client") or ("", 0)
        query = scope.get("query_string", b"").decode(errors="replace")
        push("audit", _row(
            o, method=method, path=(scope["path"] + (f"?{query}" if query else ""))[:300], status=status,
            ip=(client[0] or "")[:64],  # 反代后面请用 --proxy-headers + FORWARDED_ALLOW_IPS，这里拿到的就是真实 IP
            user_agent=headers.get("user-agent", "")[:300], request=request))
