"""证据清单：在 Agent 下结论之前，把「能拿到的硬证据」逐项查清楚。

每一项是 ok（拿到且支持判断）/ warn（拿到但相互矛盾或指向别处）/ missing（拿不到）。
清单会：
  1. 原样交给排查 Agent，让它知道哪些已证实、哪些冲突、哪些缺；
  2. 用来校准准确率（_calibrate）：缺关键证据或存在矛盾时封顶，证据齐全才放开；
  3. 展示在事件详情里，人一眼看出分数为什么是这个数、还差什么。
"""

import re
import time
from dataclasses import asdict, dataclass

import httpx

from app.agent import logs
from app.config import settings

BEFORE_MIN, AFTER_MIN = 30, 10  # 报障时间前后看多久


@dataclass
class Check:
    key: str
    label: str
    state: str  # ok / warn / missing
    detail: str


def _hm(ts: int | None) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(int(ts), ZoneInfo(settings.timezone)).strftime("%H:%M") if ts else "-"


def _codes(d: dict) -> str:
    return "、".join(f"{k}×{v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


CLAIMED = re.compile(r"(?<!\d)([45]\d\d)(?!\d)")


def claimed_codes(text: str) -> set[str]:
    """客户原话里提到的状态码（去掉 request_id 里碰巧出现的数字）。"""
    text = re.sub(r"(?:request[_ ]?id|req[_-]?id)\s*[:：=]?\s*[\w-]+", " ", text, flags=re.IGNORECASE)
    return set(CLAIMED.findall(text))


async def channel_test(channel_id: int) -> tuple[bool, str] | None:
    """调用 new-api 管理接口实时测试渠道（GET /api/channel/test/{id}）。未配置时返回 None。"""
    if not (settings.newapi_base_url and settings.newapi_admin_token):
        return None
    headers = {"Authorization": f"Bearer {settings.newapi_admin_token}"}
    if settings.newapi_admin_user_id:
        headers["New-Api-User"] = str(settings.newapi_admin_user_id)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{settings.newapi_base_url.rstrip('/')}/api/channel/test/{channel_id}",
                            headers=headers)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return False, f"测试请求失败：{type(e).__name__}"
    if data.get("success"):
        return True, f"实时测试通过，耗时 {data.get('time', '?')}s"
    return False, f"实时测试失败：{str(data.get('message', ''))[:200]}"


async def collect(text: str, request_id: str, users: list[str], model: str, report_ts: int,
                  rid_found: bool, channel_ids: list[int], knowledge_hits: int) -> list[Check]:
    checks: list[Check] = []
    db = logs.get_logdb()

    # 1. request_id
    if not request_id:
        checks.append(Check("request_id", "request_id", "missing", "客户没有提供 request_id"))
    elif rid_found:
        checks.append(Check("request_id", "request_id", "ok", f"{request_id} 在日志库中找到"))
    else:
        checks.append(Check("request_id", "request_id", "missing",
                            f"{request_id} 在日志库中查不到（可能是上游 ID、写错，或日志尚未写入）"))
    if db is None:
        checks.append(Check("logs", "日志库", "missing", "未配置日志库，无法核对"))
        return checks

    # 2. 报障时段：客户自己的调用
    w = await logs.window(db, users, model, report_ts - BEFORE_MIN * 60, report_ts + AFTER_MIN * 60)
    span = f"报障前 {BEFORE_MIN} 分钟到后 {AFTER_MIN} 分钟"
    if w is None:
        checks.append(Check("window", "报障时段", "missing", "群未绑定日志库用户（api_users），无法按客户查"))
    elif w["err"]:
        checks.append(Check("window", "报障时段", "ok",
                            f"{span}有 {w['err']} 条错误（{_codes(w['codes'])}），成功 {w['ok']} 次，"
                            f"最近一次错误 {_hm(w['last_err'])}"))
    elif w["ok"]:
        checks.append(Check("window", "报障时段", "warn",
                            f"{span}该客户 {w['ok']} 次调用全部成功，最近一次 {_hm(w['last_ok'])}，没有对应的报错"))
    else:
        checks.append(Check("window", "报障时段", "missing",
                            f"{span}该客户没有任何调用记录（可能用了未绑定的账号，或请求没到网关）"))

    # 3. 错误码是否和客户说的一致
    said = claimed_codes(text)
    seen = set((w or {}).get("codes", {})) - {"?"}
    if said and seen:
        if said & seen:
            checks.append(Check("code", "错误码一致", "ok", f"客户说 {'/'.join(sorted(said))}，日志里也是"))
        else:
            checks.append(Check("code", "错误码一致", "warn",
                                f"客户说 {'/'.join(sorted(said))}，日志里是 {'/'.join(sorted(seen))}，不是同一种错误"))

    # 4. 横向对比：同渠道同时段其他客户
    chs = list((w or {}).get("channels", {}) or {}) or channel_ids
    if chs:
        imp = await logs.channel_impact(db, chs, report_ts - BEFORE_MIN * 60, report_ts + AFTER_MIN * 60)
        top = max(imp, key=lambda x: x["err"], default=None)
        if top and top["ok"] + top["err"]:
            rate = top["err"] * 100 / (top["ok"] + top["err"])
            desc = (f"渠道 {top['channel_id']} 同时段 {top['ok'] + top['err']} 次调用，错误率 {rate:.0f}%，"
                    f"{top['users_err']}/{top['users_total']} 个客户报错（{_codes(top['codes']) or '-'}）")
            if top["users_err"] >= 2:
                checks.append(Check("impact", "横向对比", "ok", desc + "，多客户同时受影响 → 上游问题"))
            elif top["users_total"] > top["users_err"]:
                checks.append(Check("impact", "横向对比", "warn", desc + "，只有个别客户报错 → 更可能是客户侧"))
            else:
                checks.append(Check("impact", "横向对比", "missing", desc + "，同时段没有其他客户可对比"))
        else:
            checks.append(Check("impact", "横向对比", "missing", "该渠道同时段没有调用记录"))
    else:
        checks.append(Check("impact", "横向对比", "missing", "无法确定是哪个上游渠道"))

    # 5. 渠道实时测试（需配置 new-api 管理接口）
    if chs:
        r = await channel_test(chs[0])
        if r is None:
            checks.append(Check("probe", "渠道实测", "missing", "未配置 new-api 管理接口，无法实时测试渠道"))
        else:
            checks.append(Check("probe", "渠道实测", "ok" if not r[0] else "warn",
                                f"渠道 {chs[0]} {r[1]}" + ("（上游此刻正常，问题可能已恢复或在客户侧）" if r[0] else "")))

    # 6. 历史经验
    if knowledge_hits:
        checks.append(Check("knowledge", "历史经验", "ok", f"命中 {knowledge_hits} 条知识库经验"))
    return checks


def as_prompt(checks: list[Check]) -> str:
    mark = {"ok": "✓", "warn": "⚠", "missing": "✗"}
    return "\n".join(f"{mark[c.state]} {c.label}：{c.detail}" for c in checks)


def as_json(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]


def calibrate(confidence: int, checks: list[dict]) -> tuple[int, str]:
    """按证据清单给准确率封顶。返回 (新准确率, 说明)。"""
    if not checks:
        return confidence, ""
    state = {c["key"]: c["state"] for c in checks}
    strong = sum(1 for k in ("request_id", "window", "impact", "probe") if state.get(k) == "ok")
    warns = [c["label"] for c in checks if c["state"] == "warn"]
    cap, why = 100, ""
    if warns:
        cap, why = 70, f"存在矛盾证据（{'、'.join(warns)}）"
    elif strong == 0:
        cap, why = 60, "没有任何直接证据"
    elif strong == 1:
        cap, why = 80, "只有一项直接证据"
    if confidence > cap:
        return cap, f"准确率按证据封顶到 {cap}：{why}"
    return confidence, ""


def now_ts() -> int:
    return int(time.time())
