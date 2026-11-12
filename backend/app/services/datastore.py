"""数据集管理业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import field_registry
from app.store import store

MODULE = "datastore"
REQUIRED_FIELDS = ["数据集编号", "数据集名称", "来源表名"]
# 字段清单不是登记必填，但填了就留下来，字段反查面板才能给出字段身份。
OPTIONAL_FIELDS = ["字段列表"]
STATUS_ORDER = ["草稿", "待审核", "已生效", "已废弃"]
ACTION_RULES = {"提交审核": "待审核", "生效发布": "已生效", "废弃下线": "已废弃"}
NEGATIVE_ACTIONS = []


class DatastoreService:
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
            rows = [row for row in rows if keyword in str(row.get("数据集编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def list_fields(self, entry_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None, []
        return row, field_registry.ensure_fields(row)

    def rename_field(self, entry_id: int, field_id: str, new_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None, None, f"数据集定义 {entry_id} 不存在或已归档"
        field, message = field_registry.rename_field(row, field_id, new_name)
        if field is None:
            return None, None, message
        return row, field, message

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
            if str(values.get(field) or "").strip()
        })
        entry["字段版本"] = 0
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据集定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据集管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"数据集定义已{action}"
