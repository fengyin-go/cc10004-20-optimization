"""规则版本戳：凡是会改变规则语义或启用状态的写入，都留下版本与修订时间。

反查面板展示的是“规则版本快照”，消费入口再按版本比对；因此规则状态的任何流转
（提交校验、启用、停用）也必须算一次修订，否则旧选择可能在停用后仍然对得上版本。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp_rule(entry: dict[str, Any], *, initial: bool = False) -> dict[str, Any]:
    """给规则写入当前版本与更新时间。

    initial=True 用于新建规则：版本从 1 开始；其余写入都在原版本上 +1，因此
    “反查时拿到的版本”可以精确指出本次采用的是哪次修订。
    """
    if initial:
        entry["version"] = 1
    else:
        entry["version"] = int(entry.get("version") or 0) + 1
    entry["updated_at"] = now_iso()
    return entry


def rule_version(entry: dict[str, Any]) -> int:
    """版本缺省按 1 处理：兼容没有版本字段的历史规则。"""
    return int(entry.get("version") or 1)
