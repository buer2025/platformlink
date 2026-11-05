"""证据清单：报障时段、错误码一致性、横向对比，以及按证据封顶准确率。"""

from sqlalchemy import text

from app.agent import evidence
from tests.test_workflows import NOW, logdb  # noqa: F401


async def _add(db, rows):
    async with db.engine.begin() as c:
        for i, (user, typ, code, ch, ago) in enumerate(rows):
            await c.execute(text(
                "INSERT INTO logs (id,created_at,type,content,username,model_name,channel_id,other)"
                " VALUES (:id,:t,:ty,:c,:u,'seedance-1.0-pro',:ch,:o)"),
                {"id": 1000 + i, "t": NOW - ago, "ty": typ, "c": f"upstream error {code}" if code else "",
                 "u": user, "ch": ch, "o": f'{{"status_code":{code}}}' if code else "{}"})


def _by(checks):
    return {c.key: c for c in checks}


async def test_upstream_outage_is_fully_evidenced(logdb):  # noqa: F811
    await _add(logdb, [("other1", 5, 503, 7, 90), ("other2", 5, 503, 7, 100), ("other3", 2, 0, 7, 50)])
    checks = await evidence.collect("seedance 一直 503 request_id: req-abc-123", "req-abc-123",
                                    ["acme"], "seedance", NOW, rid_found=True, channel_ids=[7],
                                    knowledge_hits=0)
    c = _by(checks)
    assert c["request_id"].state == "ok" and c["window"].state == "ok"
    assert c["code"].state == "ok"
    assert c["impact"].state == "ok" and "3/4" in c["impact"].detail
    assert evidence.calibrate(95, evidence.as_json(checks)) == (95, "")


async def test_code_mismatch_and_isolated_customer_cap_confidence(logdb):  # noqa: F811
    await _add(logdb, [("other1", 2, 0, 7, 30), ("other2", 2, 0, 7, 40)])
    checks = await evidence.collect("一直报 500 了", "", ["acme"], "seedance", NOW,
                                    rid_found=False, channel_ids=[], knowledge_hits=0)
    c = _by(checks)
    assert c["request_id"].state == "missing"
    assert c["code"].state == "warn" and "500" in c["code"].detail and "503" in c["code"].detail
    assert c["impact"].state == "warn"  # 只有 acme 在报错，其他客户正常
    conf, why = evidence.calibrate(90, evidence.as_json(checks))
    assert conf == 70 and "矛盾" in why


async def test_no_calls_in_window_and_unbound_group(logdb):  # noqa: F811
    c = _by(await evidence.collect("报错", "", ["nobody"], "seedance", NOW, False, [], 0))
    assert c["window"].state == "missing" and "没有任何调用" in c["window"].detail
    c = _by(await evidence.collect("报错", "", [], "seedance", NOW, False, [], 0))
    assert "api_users" in c["window"].detail


def test_claimed_codes_ignore_request_ids():
    assert evidence.claimed_codes("request_id: req-5001-abc 报 429 了") == {"429"}
    assert evidence.claimed_codes("一直 500，偶尔 502") == {"500", "502"}
