"""数据集管理接口：维护数据集定义，覆盖提交审核、生效发布、废弃下线等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, FieldRenamePayload, PageResult, SelectionPayload
from app.services.datastore import DatastoreService
from app.services.field_references import ResolutionError, SnapshotConflict, field_reference_service

router = APIRouter(prefix="/api/datastore", tags=["数据集管理"])

service = DatastoreService()

LIST_FIELDS = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
STATUSES = ["草稿", "待审核", "已生效", "已废弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按数据集编号检索"),
    status: str | None = Query(default=None, description="草稿、待审核、已生效、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按数据集编号与状态过滤数据集管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条数据集定义明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据集定义 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/fields", response_model=dict)
def list_fields(entry_id: int) -> dict:
    """读取数据集的字段定义：字段身份、名称、别名与当前字段版本。"""
    entry, fields = service.list_fields(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据集定义 {entry_id} 不存在或已归档")
    return {
        "dataset_id": entry_id,
        "dataset_code": entry.get("数据集编号"),
        "field_version": int(entry.get("字段版本") or 0),
        "fields": fields,
    }


@router.patch("/{entry_id}/fields/{field_id}", response_model=ActionResult)
def rename_field(entry_id: int, field_id: str, payload: FieldRenamePayload) -> ActionResult:
    """字段改名：field_id 不变，旧名称留作别名，旧规则按名称关联仍可定位。"""
    entry, field, message = service.rename_field(entry_id, field_id, payload.name)
    if entry is None or field is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(
        ok=True,
        message=message,
        entry={
            "dataset_id": entry_id,
            "field_version": int(entry.get("字段版本") or 0),
            "field": field,
        },
    )


@router.get("/{entry_id}/field-references", response_model=dict)
def field_references(
    entry_id: int,
    field_id: str | None = Query(default=None, description="按字段身份定位，优先使用"),
    name: str | None = Query(default=None, description="按当前名或历史别名定位，唯一映射才解析"),
    snapshot_id: str | None = Query(default=None, description="分段取数时沿用首次返回的快照标识"),
    cursor: str | None = Query(default=None, description="上一页返回的 next_cursor"),
    size: int = Query(default=20, description="每页规则条数，最多 100"),
) -> dict:
    """字段影响反查：跨模块列出直接使用该字段和仅经组合表达式间接依赖的启用规则。"""
    try:
        return field_reference_service.lookup(
            entry_id,
            field_id=field_id,
            name=name,
            snapshot_id=snapshot_id,
            cursor=cursor,
            size=size,
        )
    except SnapshotConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "snapshot_conflict",
                "message": conflict.message,
                "current": conflict.current,
            },
        ) from conflict
    except ResolutionError as error:
        raise HTTPException(
            status_code=error.status,
            detail={
                "code": "field_resolution_failed",
                "message": error.message,
                "candidates": error.candidates,
            },
        ) from error


@router.post("/{entry_id}/field-references/consume", response_model=dict)
def consume_field_reference(entry_id: int, payload: SelectionPayload) -> dict:
    """消费点选：重新校验规则仍启用且版本未变，再登记交互查询。"""
    token = payload.selection_token
    if not token:
        raise HTTPException(status_code=400, detail="缺少 selection_token")
    return field_reference_service.consume(
        token,
        expected_dataset_id=entry_id,
        expected_snapshot_id=payload.snapshot_id,
    )


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条数据集定义，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="数据集定义已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条数据集定义执行提交审核、生效发布、废弃下线；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据集管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "datastore", "total": total, "items": items}
