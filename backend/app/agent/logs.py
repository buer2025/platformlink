"""日志库查询。真实查询方式待提供，目前为占位实现。"""

from dataclasses import dataclass


@dataclass
class LogRecord:
    request_id: str
    found: bool
    model: str = ""
    status_code: int = 0
    error: str = ""
    upstream: str = ""  # 实际路由到的供应商
    upstream_status: int = 0
    raw: str = ""


async def query_log(request_id: str) -> LogRecord:
    # TODO: 接入专用日志库（按 request_id 查询）
    return LogRecord(request_id=request_id, found=False)
