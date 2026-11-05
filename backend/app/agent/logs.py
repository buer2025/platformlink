"""日志库查询（new-api / one-api 结构，只读）。

new-api 的 logs 表主要字段：
  id, user_id, created_at(unix 秒), type(1充值 2消费 3管理 4系统 5错误 6退款), content,
  username, token_name, model_name, quota, prompt_tokens, completion_tokens, use_time,
  is_stream, channel_id, token_id, group, ip, other(JSON 文本)；新版本还有 request_id。
channels 表：id, name, type, status(1启用 2手动禁用 3自动禁用), models, base_url ...

不同版本字段略有差异，这里启动时反射表结构，缺的列自动跳过。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import MetaData, Table, func, or_, select
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

log = logging.getLogger(__name__)

TYPE_NAMES = {1: "充值", 2: "消费", 3: "管理", 4: "系统", 5: "错误", 6: "退款"}
CHANNEL_STATUS = {1: "启用", 2: "手动禁用", 3: "自动禁用"}
LOOKUPS = ("request_id", "recent_errors", "usage", "channel")


@dataclass
class Evidence:
    """一次数据查询的结果：给 Agent 读的文本 + 结构化线索。"""

    found: bool = False
    lines: list[str] = field(default_factory=list)
    channel_ids: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines) if self.lines else "未查询日志"

    def add(self, line: str) -> None:
        self.lines.append(line)


class LogDB:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self._tables: dict[str, Table | None] = {}

    async def table(self, name: str) -> Table | None:
        if name not in self._tables:
            md = MetaData()
            try:
                async with self.engine.connect() as conn:
                    self._tables[name] = await conn.run_sync(
                        lambda c: Table(name, md, autoload_with=c))
            except NoSuchTableError:
                log.warning("日志库中没有表 %s", name)
                self._tables[name] = None
        return self._tables[name]

    async def rows(self, stmt) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return [dict(r._mapping) for r in (await conn.execute(stmt)).all()]


_db: LogDB | None = None


def get_logdb() -> LogDB | None:
    global _db
    if _db is None and settings.log_database_url:
        _db = LogDB(settings.log_database_url)
    return _db


def set_logdb(db: LogDB | None) -> None:
    global _db
    _db = db


def _fmt_ts(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except (TypeError, ValueError):
        return str(ts)


def _short(v: Any, n: int = 300) -> str:
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "…"


def _fmt_log(r: dict) -> str:
    parts = [_fmt_ts(r.get("created_at")), TYPE_NAMES.get(r.get("type"), str(r.get("type")))]
    for k in ("username", "model_name", "channel_id", "use_time", "quota",
              "prompt_tokens", "completion_tokens"):
        if r.get(k) not in (None, ""):
            parts.append(f"{k}={r[k]}")
    if r.get("content"):
        parts.append(f"content={_short(r['content'])}")
    if r.get("other"):
        try:
            other = json.loads(r["other"])
            keep = {k: other[k] for k in ("status_code", "error", "error_type", "error_code",
                                          "request_path", "admin_info", "frt") if k in other}
            if keep:
                parts.append(f"other={_short(json.dumps(keep, ensure_ascii=False), 400)}")
        except (TypeError, ValueError):
            parts.append(f"other={_short(r['other'], 200)}")
    return " | ".join(parts)


def _user_filter(t: Table, users: list[str]):
    conds = []
    names = [u for u in users if u]
    ids = [int(u) for u in names if u.isdigit()]
    if "username" in t.c and names:
        conds.append(t.c.username.in_(names))
    if "user_id" in t.c and ids:
        conds.append(t.c.user_id.in_(ids))
    return or_(*conds) if conds else None


def _model_filter(t: Table, model: str):
    if model and "model_name" in t.c:
        return t.c.model_name.ilike(f"{model}%")
    return None


async def by_request_id(db: LogDB, rid: str, ev: Evidence) -> None:
    t = await db.table("logs")
    if t is None:
        ev.add("日志库不可用")
        return
    if "request_id" in t.c:
        cond = t.c.request_id == rid
    else:  # 老版本没有 request_id 列，退而在 other/content 里找（限最近 3 天）
        like = [t.c[c].contains(rid) for c in ("other", "content") if c in t.c]
        cond = or_(*like) & (t.c.created_at >= int(time.time()) - 3 * 86400)
    rows = await db.rows(select(t).where(cond).order_by(t.c.id.desc()).limit(10))
    if not rows:
        ev.add(f"[request_id] {rid}：日志库中未找到")
        return
    ev.found = True
    ev.add(f"[request_id] {rid}：找到 {len(rows)} 条")
    for r in rows:
        ev.add("  " + _fmt_log(r))
        if r.get("channel_id"):
            ev.channel_ids.append(int(r["channel_id"]))


async def recent_errors(db: LogDB, users: list[str], model: str, ev: Evidence,
                        minutes: int = 60) -> None:
    t = await db.table("logs")
    if t is None:
        return
    uf = _user_filter(t, users)
    if uf is None:
        ev.add("[近期错误] 群未绑定日志库用户（api_users），跳过")
        return
    since = int(time.time()) - minutes * 60
    conds = [uf, t.c.type == 5, t.c.created_at >= since]
    if (mf := _model_filter(t, model)) is not None:
        conds.append(mf)
    rows = await db.rows(select(t).where(*conds).order_by(t.c.id.desc()).limit(15))
    count = (await db.rows(select(func.count().label("n")).select_from(t).where(*conds)))[0]["n"]
    ev.add(f"[近期错误] 最近 {minutes} 分钟该客户{('的 ' + model) if model else ''} 错误 {count} 条")
    if rows:
        ev.found = True
        by_ch: dict[int, int] = {}
        for r in rows:
            if r.get("channel_id"):
                by_ch[int(r["channel_id"])] = by_ch.get(int(r["channel_id"]), 0) + 1
        if by_ch:
            ev.add("  按渠道分布(最近15条): " + ", ".join(f"渠道{k}×{v}" for k, v in by_ch.items()))
            ev.channel_ids.extend(sorted(by_ch, key=by_ch.get, reverse=True))
        for r in rows[:5]:
            ev.add("  " + _fmt_log(r))


async def usage(db: LogDB, users: list[str], model: str, ev: Evidence, hours: int = 24) -> None:
    t = await db.table("logs")
    if t is None:
        return
    uf = _user_filter(t, users)
    if uf is None:
        ev.add("[用量] 群未绑定日志库用户（api_users），跳过")
        return
    since = int(time.time()) - hours * 3600
    conds = [uf, t.c.created_at >= since, t.c.type.in_([2, 5])]
    if (mf := _model_filter(t, model)) is not None:
        conds.append(mf)
    cols = [t.c.model_name, t.c.type, func.count().label("n")]
    for c in ("quota", "prompt_tokens", "completion_tokens"):
        if c in t.c:
            cols.append(func.coalesce(func.sum(t.c[c]), 0).label(c))
    rows = await db.rows(select(*cols).where(*conds).group_by(t.c.model_name, t.c.type))
    if not rows:
        ev.add(f"[用量] 最近 {hours} 小时无调用记录")
        return
    ev.found = True
    stats: dict[str, dict] = {}
    for r in rows:
        s = stats.setdefault(r["model_name"] or "?", {"ok": 0, "err": 0, "quota": 0, "tokens": 0})
        if r["type"] == 2:
            s["ok"] += r["n"]
            s["quota"] += int(r.get("quota") or 0)
            s["tokens"] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)
        else:
            s["err"] += r["n"]
    ev.add(f"[用量] 最近 {hours} 小时：")
    for m, s in sorted(stats.items(), key=lambda kv: -kv[1]["ok"]):
        total = s["ok"] + s["err"]
        rate = f"{s['err'] * 100 / total:.1f}%" if total else "-"
        ev.add(f"  {m}: 成功 {s['ok']} 次，错误 {s['err']} 次（错误率 {rate}），"
               f"tokens {s['tokens']}，quota {s['quota']}，日均 RPM≈{s['ok'] / (hours * 60):.2f}")


async def channels(db: LogDB, ids: list[int], ev: Evidence) -> None:
    t = await db.table("channels")
    if t is None or not ids:
        return
    rows = await db.rows(select(t).where(t.c.id.in_(list(dict.fromkeys(ids))[:5])))
    for r in rows:
        status = CHANNEL_STATUS.get(r.get("status"), r.get("status"))
        extra = f"，响应 {r['response_time']}ms" if r.get("response_time") else ""
        ev.add(f"[渠道] {r['id']} {r.get('name', '')}：{status}{extra}")


async def lookup(steps: list[str], request_id: str, users: list[str], model: str) -> Evidence:
    """按链路配置的步骤查询日志库。任何一步失败都不影响其他步骤。"""
    ev = Evidence()
    db = get_logdb()
    if db is None:
        ev.add("日志库未配置（LOG_DATABASE_URL），无法查询")
        return ev
    for step in steps:
        try:
            if step == "request_id":
                if request_id:
                    await by_request_id(db, request_id, ev)
                else:
                    ev.add("[request_id] 客户未提供")
            elif step == "recent_errors":
                await recent_errors(db, users, model, ev)
            elif step == "usage":
                await usage(db, users, model, ev)
            elif step == "channel":
                await channels(db, ev.channel_ids, ev)
        except Exception as e:
            log.exception("日志查询 %s 失败", step)
            ev.add(f"[{step}] 查询失败：{type(e).__name__}")
    return ev


async def channel_429(db: LogDB, channel_ids: list[int], hours: int = 24) -> dict:
    """某些渠道最近的 429 与错误数量，用于催量。"""
    t = await db.table("logs")
    if t is None or not channel_ids or "channel_id" not in t.c:
        return {"rate_limited": 0, "errors": 0, "success": 0}
    since = int(time.time()) - hours * 3600
    base = [t.c.channel_id.in_(channel_ids), t.c.created_at >= since]
    is429 = or_(*[t.c[c].contains("429") for c in ("content", "other") if c in t.c])
    q = lambda *conds: select(func.count().label("n")).select_from(t).where(*base, *conds)
    return {
        "rate_limited": (await db.rows(q(t.c.type == 5, is429)))[0]["n"],
        "errors": (await db.rows(q(t.c.type == 5)))[0]["n"],
        "success": (await db.rows(q(t.c.type == 2)))[0]["n"],
    }
