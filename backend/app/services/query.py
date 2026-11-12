"""交互查询业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "query"
REQUIRED_FIELDS = ["语句编号", "查询表达式", "目标数据集"]
# 从筛选/聚合/分片规则消费来的查询要保留依据，便于回溯本次采用规则版本与字段身份。
OPTIONAL_FIELDS = [
    "返回字段", "排序条件", "分页大小", "最大行数", "超时秒数",
    "来源规则编号", "来源规则版本", "来源字段标识", "数据集字段版本",
]
STATUS_ORDER = ["草稿", "待执行", "执行中", "已完成", "已超时"]
ACTION_RULES = {"提交执行": "待执行", "启动查询": "执行中", "终止查询": "已超时", "保存结果": "已完成"}
NEGATIVE_ACTIONS = []


class QueryService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("语句编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry.update({
            field: values.get(field)
            for field in OPTIONAL_FIELDS
            if values.get(field) not in (None, "")
        })
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"查询语句 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于交互查询可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"查询语句已{action}"
