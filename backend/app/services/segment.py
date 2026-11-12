"""数据分片业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.rule_versions import stamp_rule
from app.store import store

MODULE = "segment"
REQUIRED_FIELDS = ["规则编号", "目标数据集", "分片字段"]
FIELD_NAME_ID_PAIRS = [("分片字段", "分片字段标识")]
EDITABLE_FIELDS = ["目标数据集", "分片字段", "分片字段标识", "分片算法", "分片数量", "数据倾斜阈值", "再平衡触发", "迁移策略"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用分片": "已启用", "停用分片": "已停用"}
NEGATIVE_ACTIONS = ["停用分片"]


class SegmentService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        stamp_rule(entry, initial=True)
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据分片记录 {entry_id} 不存在或已归档"
        if str(entry.get("status") or "") == STATUS_ORDER[-1]:
            return None, "已停用的数据分片规则不能直接修订，请先重新启用"
        updates = {field: values.get(field) for field in EDITABLE_FIELDS if field in values}
        for field in REQUIRED_FIELDS:
            if field in updates and not str(updates[field] or "").strip():
                return None, f"必填字段「{field}」不能改为空"
        if not updates:
            return None, "本次修订没有可更新的字段"
        # 只改名称、没给新标识时清掉旧标识：不能让一个旧 field_id 继续指向别的字段，
        # 反查会退回名称唯一映射，命中多个则列入待确认。
        for name_key, id_key in FIELD_NAME_ID_PAIRS:
            if name_key in updates and id_key not in updates:
                updates[id_key] = None
        entry.update(updates)
        stamp_rule(entry)
        return entry, f"规则已修订，当前版本 {entry['version']}"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分片规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据分片可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        stamp_rule(entry)
        return entry, f"分片规则已{action}"
