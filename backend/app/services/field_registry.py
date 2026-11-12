"""数据集字段注册表：字段身份、名称/别名解析与改名后的历史名称。

字段身份以 field_id 为准，名称只是展示与旧规则兼容用的别名线索：
- 同一个名称在不同数据集里是不同字段（DATA-0001 的 amount 与 DATA-0002 的 amount 不能混用）；
- 改名时把旧名称写进 aliases，旧规则按名称关联仍能定位；
- 同一数据集内一个名称命中多个字段时不做猜测，交给反查面板标记为待确认。
"""
from __future__ import annotations

import re
from typing import Any

from app.store import store

DATASET_MODULE = "datastore"

_SPLIT_RE = re.compile(r"[,，;；\s]+")


def normalize_name(raw: Any) -> str:
    return str(raw or "").strip().lower()


def dataset_rows() -> list[dict[str, Any]]:
    return store.rows(DATASET_MODULE)


def find_dataset_by_code(code: str | None) -> dict[str, Any] | None:
    target = str(code or "").strip()
    if not target:
        return None
    for row in dataset_rows():
        if str(row.get("数据集编号") or "").strip() == target:
            return row
    return None


def find_dataset_by_id(entry_id: int) -> dict[str, Any] | None:
    return store.find(DATASET_MODULE, entry_id)


def find_dataset_by_ref(value: Any) -> dict[str, Any] | None:
    """规则里的目标数据集可能是编号，也可能是历史遗留的数据集名称。"""
    target = str(value or "").strip()
    if not target:
        return None
    for row in dataset_rows():
        if dataset_code(row) == target or str(row.get("数据集名称") or "").strip() == target:
            return row
    return None


def dataset_code(row: dict[str, Any]) -> str:
    return str(row.get("数据集编号") or "").strip()


def _synthetic_field(field_name: str, index: int) -> dict[str, Any]:
    return {
        "field_id": f"fld_auto_{index}",
        "name": field_name,
        "label": field_name,
        "type": "unknown",
        "aliases": [],
    }


def ensure_fields(row: dict[str, Any]) -> list[dict[str, Any]]:
    """取数据集的字段定义；只有名称清单时补齐成字段身份。"""
    raw = row.get("字段定义")
    if not isinstance(raw, list) or not raw:
        names = [item for item in _SPLIT_RE.split(str(row.get("字段列表") or "")) if item]
        raw = [_synthetic_field(name, index) for index, name in enumerate(names)]
        row["字段定义"] = raw
    fields: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        field_id = str(item.get("field_id") or "").strip() or f"fld_auto_{index}"
        # 原地规范化并返回同一个 dict：改名等写入要直接落到数据集定义上，
        # 不能只改临时副本，否则字段版本递增了但名称没变。
        item["field_id"] = field_id
        item["name"] = name
        item["label"] = str(item.get("label") or name)
        item["type"] = str(item.get("type") or "unknown")
        item["aliases"] = [str(alias).strip() for alias in item.get("aliases") or [] if str(alias).strip()]
        fields.append(item)
    return fields


def field_by_id(fields: list[dict[str, Any]], field_id: str | None) -> dict[str, Any] | None:
    target = str(field_id or "").strip()
    for field in fields:
        if field["field_id"] == target:
            return field
    return None


def match_field_ids(fields: list[dict[str, Any]], raw_name: Any) -> list[str]:
    """按名称或历史别名找字段身份；返回候选，命中多个时由调用方留待确认。"""
    key = normalize_name(raw_name)
    if not key:
        return []
    matched: list[str] = []
    for field in fields:
        names = {normalize_name(field["name"])} | {normalize_name(alias) for alias in field["aliases"]}
        if key in names and field["field_id"] not in matched:
            matched.append(field["field_id"])
    return matched


def match_kind(field: dict[str, Any], raw_name: Any) -> str:
    """说明这次名称命中走的是当前名还是历史别名，便于面板展示兼容来源。"""
    key = normalize_name(raw_name)
    if key == normalize_name(field["name"]):
        return "name"
    return "alias"


def rename_field(
    row: dict[str, Any], field_id: str, new_name: str
) -> tuple[dict[str, Any] | None, str]:
    """字段改名：field_id 不变，旧名称留作别名，字段版本递增。"""
    fields = ensure_fields(row)
    field = field_by_id(fields, field_id)
    if field is None:
        return None, f"字段 {field_id} 不在数据集字段定义中"
    name = str(new_name or "").strip()
    if not name:
        return None, "新字段名不能为空"
    key = normalize_name(name)
    for other in fields:
        if other["field_id"] == field_id:
            continue
        names = {normalize_name(other["name"])} | {normalize_name(alias) for alias in other["aliases"]}
        if key in names:
            return None, f"字段名「{name}」已被字段 {other['field_id']} 使用，改名会造成歧义"
    old_name = field["name"]
    field["name"] = name
    if old_name and old_name != name:
        aliases = {normalize_name(alias) for alias in field["aliases"]}
        if old_name not in field["aliases"] and normalize_name(old_name) not in aliases:
            field["aliases"] = [*field["aliases"], old_name]
    # 同步展示用的字段清单，避免列表与字段定义对不上
    if isinstance(row.get("字段列表"), str) and row["字段列表"]:
        names = [item["name"] for item in fields]
        row["字段列表"] = ", ".join(names)
    row["字段版本"] = int(row.get("字段版本") or 0) + 1
    return field, f"字段 {field_id} 已更名为 {name}，历史名称保留为别名"
