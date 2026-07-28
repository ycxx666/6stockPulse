"""
api/_utils.py — 共享工具函数
"""
from __future__ import annotations
from datetime import datetime, date
from decimal import Decimal
from typing import Any


def _jsonable(obj: Any):
    """将 asyncpg 返回值转为 JSON 可序列化对象"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj
