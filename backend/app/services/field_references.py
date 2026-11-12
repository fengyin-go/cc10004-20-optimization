"""字段影响反查：给定数据集字段，跨模块检索哪些启用规则直接使用它、哪些经组合表达式间接依赖。

一致性边界：
- 字段身份以 field_id 为准；id 缺失或失效时，只在规则所属数据集内按当前名/历史别名做唯一映射，
  同一名称命中多个字段则不猜测，列入“待确认”；
- 表达式按完整标识符分词匹配，amount 不会命中 amount_total；
- 每条规则只产生一条关系（直接使用优先于间接依赖），多分支表达式只累计命中次数；
- 快照 id 由“数据集字段版本 + 相关规则版本/状态”指纹决定。分页请求必须带同一 snapshot_id，
  指纹变化即 409 并回传当前版本，避免旧页与新规则混在一起；
- 反查响应对每条规则给出本次采用的 rule_version；点选规则时签发一次性 selection_token，
  消费前重新校验规则仍为启用状态且版本未变，已停用或已修订的旧选择不能再次进入交互查询。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.services import field_registry
from app.services.query import QueryService
from app.services.rule_versions import now_iso, rule_version
from app.store import store

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class RuleSource:
    module: str
    label: str
    rule_no_key: str
    dataset_key: str
    enabled_status: str
    direct: tuple[tuple[str, str, str], ...]  # (名称字段, 标识字段, 角色)
    expressions: tuple[tuple[str, str], ...]  # (表达式字段, 展示名)


RULE_SOURCES: tuple[RuleSource, ...] = (
    RuleSource(
        module="filter",
        label="数据筛选",
        rule_no_key="规则编号",
        dataset_key="目标数据集",
        enabled_status="已启用",
        direct=(
            ("筛选字段", "筛选字段标识", "筛选字段"),
            ("排序字段", "排序字段标识", "排序字段"),
        ),
        expressions=(("组合表达式", "组合表达式"),),
    ),
    RuleSource(
        module="aggregate",
        label="聚合计算",
        rule_no_key="方案编号",
        dataset_key="目标数据集",
        enabled_status="已启用",
        direct=(
            ("目标字段", "目标字段标识", "目标字段"),
            ("分组维度", "", "分组维度"),
        ),
        expressions=(("筛选条件", "筛选条件"),),
    ),
    RuleSource(
        module="segment",
        label="数据分片",
        rule_no_key="规则编号",
        dataset_key="目标数据集",
        enabled_status="已启用",
        direct=(("分片字段", "分片字段标识", "分片字段"),),
        expressions=(),
    ),
)

RULE_SOURCES_BY_MODULE = {source.module: source for source in RULE_SOURCES}

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
TICKET_TTL_SECONDS = 1800
TICKET_CAPACITY = 1000


class SnapshotConflict(Exception):
    """分页期间规则或字段定义发生变化，本次快照不再可用。"""

    def __init__(self, message: str, current: dict[str, Any]) -> None:
        super().__init__(message)
        self.message = message
        self.current = current


class ResolutionError(Exception):
    """字段定位失败：找不到或名称有歧义。"""

    def __init__(self, message: str, *, candidates: list[dict[str, Any]] | None = None, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.candidates = candidates or []
        self.status = status


@dataclass
class SelectionTicket:
    token: str
    snapshot_id: str
    dataset_id: int
    dataset_code: str
    dataset_field_version: int
    field_id: str
    field_name: str
    module: str
    rule_id: int
    rule_no: str
    rule_version: int
    created_at: float
    consumed: bool = False
    consumed_at: str | None = None


def _scope_rules() -> list[tuple[RuleSource, dict[str, Any]]]:
    scoped: list[tuple[RuleSource, dict[str, Any]]] = []
    for source in RULE_SOURCES:
        for rule in store.rows(source.module):
            scoped.append((source, rule))
    return scoped


def _rule_dataset(source: RuleSource, rule: dict[str, Any]) -> dict[str, Any] | None:
    return field_registry.find_dataset_by_ref(rule.get(source.dataset_key))


def _dataset_fingerprint(dataset: dict[str, Any]) -> str:
    code = field_registry.dataset_code(dataset)
    version = int(dataset.get("字段版本") or 0)
    return f"dataset:{code}:v{version}"


def _rule_index_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source, rule in _scope_rules():
        rows.append({
            "module": source.module,
            "module_label": source.label,
            "rule_id": int(rule.get("id") or 0),
            "rule_no": str(rule.get(source.rule_no_key) or rule.get("id") or ""),
            "rule_version": rule_version(rule),
            "status": str(rule.get("status") or ""),
            "updated_at": str(rule.get("updated_at") or ""),
        })
    return rows


def _snapshot_fingerprint(dataset: dict[str, Any], field_id: str, rule_rows: list[dict[str, Any]]) -> str:
    rules = ";".join(
        f"{row['module']}:{row['rule_id']}:{row['rule_version']}:{row['status']}"
        for row in rule_rows
    )
    return f"{_dataset_fingerprint(dataset)}|field:{field_id}|{rules}"


def _snapshot_id(fingerprint: str) -> str:
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


def _expression_hits(rule: dict[str, Any], source: RuleSource, fields: list[dict[str, Any]], field_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (命中明细, 歧义项)；按完整标识符匹配，amount 不会命中 amount_total。"""
    hits: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for expression_key, expression_label in source.expressions:
        expression = str(rule.get(expression_key) or "")
        if not expression:
            continue
        counts: dict[str, int] = {}
        seen_ambiguous: set[str] = set()
        for token in _TOKEN_RE.findall(expression):
            candidates = field_registry.match_field_ids(fields, token)
            if field_id not in candidates:
                continue
            counts[token] = counts.get(token, 0) + 1
            if len(candidates) > 1 and token not in seen_ambiguous:
                seen_ambiguous.add(token)
                ambiguous.append(_ambiguity(
                    source,
                    rule,
                    declared_name=token,
                    role=expression_label,
                    candidates=candidates,
                    fields=fields,
                    reason="表达式中的名称在同一数据集内命中多个字段，需确认指向",
                ))
        for token, occurrences in counts.items():
            candidates = field_registry.match_field_ids(fields, token)
            if len(candidates) == 1:
                matched_field = field_registry.field_by_id(fields, candidates[0])
                hits.append({
                    "key": expression_key,
                    "label": expression_label,
                    "token": token,
                    "matched_by": field_registry.match_kind(matched_field, token) if matched_field else "name",
                    "matched_name": matched_field["name"] if matched_field else token,
                    "occurrences": occurrences,
                })
    return hits, ambiguous


def _ambiguity(
    source: RuleSource,
    rule: dict[str, Any],
    *,
    declared_name: str,
    role: str,
    candidates: list[str],
    fields: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "module": source.module,
        "module_label": source.label,
        "rule_id": int(rule.get("id") or 0),
        "rule_no": str(rule.get(source.rule_no_key) or rule.get("id") or ""),
        "rule_version": rule_version(rule),
        "rule_updated_at": str(rule.get("updated_at") or ""),
        "rule_status": str(rule.get("status") or ""),
        "field_name": declared_name,
        "role": role,
        "reason": reason,
        "needs_confirmation": True,
        "candidates": [
            {
                "field_id": field_id,
                "name": (field_registry.field_by_id(fields, field_id) or {}).get("name", field_id),
                "label": (field_registry.field_by_id(fields, field_id) or {}).get("label", field_id),
            }
            for field_id in candidates
        ],
    }


def _resolve_declared(
    source: RuleSource,
    rule: dict[str, Any],
    fields: list[dict[str, Any]],
    *,
    value_key: str,
    id_key: str,
    role: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """解析规则字符串字段指向的字段身份；返回 (使用明细, 歧义项)。"""
    declared_name = str(rule.get(value_key) or "").strip()
    declared_id = str(rule.get(id_key) or "").strip() if id_key else ""
    if not declared_name and not declared_id:
        return None, None

    matched: dict[str, Any] | None = None
    matched_by = ""
    if declared_id:
        matched = field_registry.field_by_id(fields, declared_id)
        if matched is not None:
            matched_by = "id"

    candidates: list[str] = []
    if matched is None and declared_name:
        candidates = field_registry.match_field_ids(fields, declared_name)
        if len(candidates) == 1:
            matched = field_registry.field_by_id(fields, candidates[0])
            matched_by = field_registry.match_kind(matched, declared_name) if matched else "name"

    if matched is not None:
        return ({
            "role": role,
            "declared_name": declared_name or matched["name"],
            "declared_field_id": declared_id or None,
            "field_id": matched["field_id"],
            "matched_by": matched_by,
            "matched_name": matched["name"],
        }, None)

    if not declared_name:
        return None, None
    if len(candidates) > 1:
        reason = f"名称「{declared_name}」在同一数据集内命中多个字段，需确认指向"
        item = _ambiguity(source, rule, declared_name=declared_name, role=role,
                           candidates=candidates, fields=fields, reason=reason)
        item["declared_field_id"] = declared_id or None
        return None, item
    reason = f"字段标识 {declared_id} 不属于目标数据集，且名称「{declared_name}」未命中任何字段" \
        if declared_id else f"名称「{declared_name}」未命中目标数据集的任何字段"
    ambiguity = _ambiguity(source, rule, declared_name=declared_name, role=role,
                           candidates=candidates, fields=fields, reason=reason)
    ambiguity["reason_kind"] = "unresolved"
    return None, ambiguity


def _relationship_for_rule(
    source: RuleSource,
    rule: dict[str, Any],
    dataset: dict[str, Any],
    fields: list[dict[str, Any]],
    field_id: str,
    field_name: str,
) -> dict[str, Any] | None:
    """为一个启用规则计算它和目标字段的关系；同一个规则最多返回一条关系。"""
    usages: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    for value_key, id_key, role in source.direct:
        usage, ambiguity = _resolve_declared(
            source, rule, fields, value_key=value_key, id_key=id_key, role=role
        )
        if ambiguity is not None:
            ambiguities.append(ambiguity)
        if usage is not None and usage["field_id"] == field_id:
            usages.append(usage)

    hits, hit_ambiguities = _expression_hits(rule, source, fields, field_id)
    ambiguities.extend(hit_ambiguities)

    direct = bool(usages)
    indirect = bool(hits)
    if not direct and not indirect:
        return {
            "relation": None,
            "rule": rule,
            "ambiguities": ambiguities,
            "source": source,
        }

    relation = "direct" if direct else "indirect"
    return {
        "relation": relation,
        "also_indirect": direct and indirect,
        "source": source,
        "rule": rule,
        "dataset": dataset,
        "field_id": field_id,
        "field_name": field_name,
        "usages": usages,
        "expression_hits": hits,
        "ambiguities": ambiguities,
    }


def _relation_payload(item: dict[str, Any], selection_token: str | None) -> dict[str, Any]:
    source: RuleSource = item["source"]
    rule: dict[str, Any] = item["rule"]
    dataset: dict[str, Any] = item["dataset"]
    expressions = [
        f"{hit['label']}·{hit['token']}×{hit['occurrences']}" for hit in item["expression_hits"]
    ]
    if item["relation"] == "direct":
        summary = "直接使用：" + "、".join(
            f"{usage['role']}={usage['matched_name']}" for usage in item["usages"]
        )
    else:
        summary = "间接依赖：" + "、".join(expressions)
    return {
        "module": source.module,
        "module_label": source.label,
        "rule_id": int(rule.get("id") or 0),
        "rule_no": str(rule.get(source.rule_no_key) or rule.get("id") or ""),
        "rule_version": rule_version(rule),
        "rule_updated_at": str(rule.get("updated_at") or ""),
        "rule_status": str(rule.get("status") or ""),
        "dataset_code": field_registry.dataset_code(dataset),
        "field_id": item["field_id"],
        "field_name": item["field_name"],
        "relation": item["relation"],
        "also_indirect": bool(item.get("also_indirect")),
        "usages": item["usages"],
        "expression_hits": item["expression_hits"],
        "summary": summary,
        "selection_token": selection_token,
    }


class FieldReferenceService:
    """反向索引按快照指纹缓存：同一字段同一规则版本下重复翻页不再重扫全部规则。

    指纹变化（字段改名/规则修订/启用停用）即视为新索引；扫描前后各取一次规则指纹，
    中途发生变化就重试，避免一页结果混入两个版本。
    """

    CACHE_CAPACITY = 64

    def __init__(self) -> None:
        self._tickets: OrderedDict[str, SelectionTicket] = OrderedDict()
        self._index_cache: OrderedDict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = OrderedDict()
        self._query_service = QueryService()

    def _cache_get(self, key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        found = self._index_cache.get(key)
        if found is not None:
            self._index_cache.move_to_end(key)
        return found

    def _cache_put(self, key: str, value: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
        self._index_cache[key] = value
        self._index_cache.move_to_end(key)
        while len(self._index_cache) > self.CACHE_CAPACITY:
            self._index_cache.popitem(last=False)

    # ---- 字段定位 -------------------------------------------------------
    def resolve_field(
        self,
        dataset_entry_id: int,
        *,
        field_id: str | None = None,
        name: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        dataset = field_registry.find_dataset_by_id(dataset_entry_id)
        if dataset is None:
            raise ResolutionError(f"数据集定义 {dataset_entry_id} 不存在或已归档", status=404)
        fields = field_registry.ensure_fields(dataset)

        if field_id:
            field = field_registry.field_by_id(fields, field_id)
            if field is None:
                raise ResolutionError(
                    f"字段标识 {field_id} 不在数据集 {field_registry.dataset_code(dataset)} 的字段定义中",
                    status=404,
                )
            return dataset, field

        if name:
            candidates = field_registry.match_field_ids(fields, name)
            if len(candidates) == 1:
                return dataset, field_registry.field_by_id(fields, candidates[0])  # type: ignore[return-value]
            if len(candidates) > 1:
                payload = [
                    {"field_id": candidate, "name": (field_registry.field_by_id(fields, candidate) or {}).get("name", candidate)}
                    for candidate in candidates
                ]
                raise ResolutionError(
                    f"名称「{name}」在同一数据集内命中多个字段，请改用字段标识定位",
                    candidates=payload,
                    status=409,
                )
            raise ResolutionError(f"字段名称「{name}」未命中数据集字段定义", status=404)

        raise ResolutionError("需要提供 field_id 或 name 定位字段", status=400)

    # ---- 快照与检索 -----------------------------------------------------
    def _scan_relations(
        self,
        dataset: dict[str, Any],
        fields: list[dict[str, Any]],
        target_field: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """扫描启用规则建立反向关系；扫描前后各取一次规则指纹，中途变化就重试一次。"""
        dataset_code = field_registry.dataset_code(dataset)
        for attempt in range(2):
            before = _snapshot_fingerprint(dataset, target_field["field_id"], _rule_index_rows())
            relations: list[dict[str, Any]] = []
            ambiguous: list[dict[str, Any]] = []
            for source, rule in _scope_rules():
                rule_dataset = _rule_dataset(source, rule)
                if rule_dataset is None or field_registry.dataset_code(rule_dataset) != dataset_code:
                    continue
                if str(rule.get("status") or "") != source.enabled_status:
                    continue
                relationship = _relationship_for_rule(
                    source,
                    rule,
                    dataset,
                    fields,
                    target_field["field_id"],
                    target_field["name"],
                )
                if relationship is None:
                    continue
                # 只保留与本面板字段可能相关的歧义项：候选里含目标字段，才需要用户确认指向。
                ambiguous.extend(
                    item for item in relationship["ambiguities"]
                    if any(candidate["field_id"] == target_field["field_id"] for candidate in item["candidates"])
                )
                if relationship["relation"] is None:
                    continue
                relations.append(relationship)

            after = _snapshot_fingerprint(dataset, target_field["field_id"], _rule_index_rows())
            if before == after:
                module_order = {source.module: index for index, source in enumerate(RULE_SOURCES)}
                relations.sort(key=lambda item: (
                    module_order.get(item["source"].module, len(module_order)),
                    str(item["rule"].get(item["source"].rule_no_key) or item["rule"].get("id") or ""),
                    int(item["rule"].get("id") or 0),
                ))
                return relations, ambiguous
        raise SnapshotConflict(
            "反查期间规则版本持续变化，本次结果不可用，请稍后刷新重试",
            {
                "current_snapshot_id": _snapshot_id(
                    _snapshot_fingerprint(dataset, target_field["field_id"], _rule_index_rows())
                ),
                "dataset_field_version": int(dataset.get("字段版本") or 0),
                "dataset_code": dataset_code,
                "built_at": now_iso(),
            },
        )

    def _build_snapshot(
        self,
        dataset: dict[str, Any],
        target_field: dict[str, Any],
        *,
        requested_snapshot_id: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        fields = field_registry.ensure_fields(dataset)
        rule_rows = _rule_index_rows()
        fingerprint = _snapshot_fingerprint(dataset, target_field["field_id"], rule_rows)
        snapshot_id = _snapshot_id(fingerprint)
        if requested_snapshot_id and requested_snapshot_id != snapshot_id:
            raise SnapshotConflict(
                "规则版本或字段定义已变化，本页快照失效，请刷新反查结果",
                {
                    "current_snapshot_id": snapshot_id,
                    "dataset_field_version": int(dataset.get("字段版本") or 0),
                    "dataset_code": field_registry.dataset_code(dataset),
                    "rule_versions": {
                        f"{row['module']}:{row['rule_id']}": {
                            "rule_no": row["rule_no"],
                            "version": row["rule_version"],
                            "status": row["status"],
                        }
                        for row in rule_rows
                    },
                    "built_at": now_iso(),
                },
            )

        cached = self._cache_get(fingerprint)
        if cached is None:
            cached = self._scan_relations(dataset, fields, target_field)
            self._cache_put(fingerprint, cached)
        relations, ambiguous = cached

        snapshot = {
            "id": snapshot_id,
            "built_at": now_iso(),
            "dataset": {
                "id": int(dataset.get("id") or 0),
                "code": field_registry.dataset_code(dataset),
                "name": str(dataset.get("数据集名称") or ""),
                "field_version": int(dataset.get("字段版本") or 0),
            },
            "field": {
                "field_id": target_field["field_id"],
                "name": target_field["name"],
                "label": target_field["label"],
                "type": target_field["type"],
                "aliases": target_field["aliases"],
            },
            "rule_versions": {
                f"{row['module']}:{row['rule_id']}": {
                    "module": row["module"],
                    "module_label": row["module_label"],
                    "rule_no": row["rule_no"],
                    "version": row["rule_version"],
                    "status": row["status"],
                    "updated_at": row["updated_at"],
                }
                for row in rule_rows
            },
        }
        return snapshot, relations, ambiguous

    def lookup(
        self,
        dataset_entry_id: int,
        *,
        field_id: str | None = None,
        name: str | None = None,
        snapshot_id: str | None = None,
        cursor: str | None = None,
        size: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        size = max(1, min(size, MAX_PAGE_SIZE))
        if cursor and not snapshot_id:
            # 没带快照的续页会在规则变化后悄悄拼出一个新版本，宁可让调用方重来。
            raise ResolutionError("续页请求必须携带首次反查返回的 snapshot_id，避免新旧版本混排", status=400)
        dataset, target_field = self.resolve_field(dataset_entry_id, field_id=field_id, name=name)
        snapshot, relations, ambiguous = self._build_snapshot(
            dataset, target_field, requested_snapshot_id=snapshot_id
        )

        offset = self._parse_cursor(cursor)
        page = relations[offset:offset + size]
        next_offset = offset + len(page)
        next_cursor = f"offset:{next_offset}" if next_offset < len(relations) else None

        direct_total = sum(1 for item in relations if item["relation"] == "direct")
        indirect_total = len(relations) - direct_total
        items = [
            _relation_payload(item, self._issue_ticket(snapshot, dataset, target_field, item))
            for item in page
        ]
        return {
            "snapshot": snapshot,
            "summary": {
                "direct": direct_total,
                "indirect": indirect_total,
                "ambiguous": len(ambiguous),
                "relations_total": len(relations),
            },
            "relations": items,
            "ambiguous": ambiguous if offset == 0 else [],
            "ambiguous_total": len(ambiguous),
            "page": {
                "size": size,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "returned": len(items),
                "total": len(relations),
            },
        }

    @staticmethod
    def _parse_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        prefix = "offset:"
        if not cursor.startswith(prefix):
            raise ResolutionError(f"分页游标 {cursor} 无法识别，请重新发起反查", status=400)
        try:
            value = int(cursor[len(prefix):])
        except ValueError as error:
            raise ResolutionError(f"分页游标 {cursor} 不是合法偏移", status=400) from error
        if value < 0:
            raise ResolutionError("分页游标不能为负数", status=400)
        return value

    # ---- 点选凭据与消费 -------------------------------------------------
    def _issue_ticket(
        self,
        snapshot: dict[str, Any],
        dataset: dict[str, Any],
        target_field: dict[str, Any],
        item: dict[str, Any],
    ) -> str:
        self._prune_tickets()
        source: RuleSource = item["source"]
        rule: dict[str, Any] = item["rule"]
        token = secrets.token_urlsafe(12)
        ticket = SelectionTicket(
            token=token,
            snapshot_id=snapshot["id"],
            dataset_id=int(dataset.get("id") or 0),
            dataset_code=field_registry.dataset_code(dataset),
            dataset_field_version=int(dataset.get("字段版本") or 0),
            field_id=target_field["field_id"],
            field_name=target_field["name"],
            module=source.module,
            rule_id=int(rule.get("id") or 0),
            rule_no=str(rule.get(source.rule_no_key) or rule.get("id") or ""),
            rule_version=rule_version(rule),
            created_at=time.monotonic(),
        )
        self._tickets[token] = ticket
        while len(self._tickets) > TICKET_CAPACITY:
            self._tickets.popitem(last=False)
        return token

    def _prune_tickets(self) -> None:
        now = time.monotonic()
        expired = [
            token for token, ticket in self._tickets.items()
            if now - ticket.created_at > TICKET_TTL_SECONDS
        ]
        for token in expired:
            self._tickets.pop(token, None)

    def _reject_stale(self, ticket: SelectionTicket, current_version: int, reason: str) -> dict[str, Any]:
        ticket.consumed = True  # 旧选择作废，需要从最新反查结果重新点选
        ticket.consumed_at = now_iso()
        return {
            "ok": False,
            "code": "selection_stale",
            "message": reason,
            "rule_version": ticket.rule_version,
            "current_rule_version": current_version,
            "rule_id": ticket.rule_id,
            "rule_no": ticket.rule_no,
        }

    def consume(
        self,
        token: str,
        *,
        expected_dataset_id: int | None = None,
        expected_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """消费点选：重新校验启用依据与版本，再走既有交互查询登记流程。

        expected_* 是调用方声明的上下文：与签发的选择不一致时直接拒绝，
        避免面板上换了数据集/刷新过快后，旧凭据被用到新定位上。
        """
        self._prune_tickets()
        ticket = self._tickets.get(token)
        if ticket is None:
            return {
                "ok": False,
                "code": "selection_expired",
                "message": "该选择已过期或不存在，请从最新反查结果重新点选",
            }
        if ticket.consumed:
            return {
                "ok": False,
                "code": "selection_consumed",
                "message": "该选择已经消费过，不能重复进入交互查询",
            }
        if expected_dataset_id is not None and int(expected_dataset_id) != ticket.dataset_id:
            return {
                "ok": False,
                "code": "selection_context_mismatch",
                "message": "该选择对应的数据集与当前面板不一致，请重新反查",
            }
        if expected_snapshot_id and expected_snapshot_id != ticket.snapshot_id:
            return {
                "ok": False,
                "code": "selection_context_mismatch",
                "message": "该选择来自上一次反查快照，当前面板已刷新，请重新点选",
            }

        source = RULE_SOURCES_BY_MODULE.get(ticket.module)
        if source is None:
            return {"ok": False, "code": "rule_missing", "message": f"规则模块 {ticket.module} 不在反查范围内"}

        rule = store.find(source.module, ticket.rule_id)
        if rule is None:
            ticket.consumed = True
            ticket.consumed_at = now_iso()
            return {
                "ok": False,
                "code": "rule_missing",
                "message": f"规则 {ticket.rule_no} 不存在或已归档",
            }
        current_version = rule_version(rule)
        if str(rule.get("status") or "") != source.enabled_status:
            ticket.consumed = True
            ticket.consumed_at = now_iso()
            return {
                "ok": False,
                "code": "rule_disabled",
                "message": f"规则 {ticket.rule_no} 当前状态为「{rule.get('status') or '未知'}」，已不能作为交互查询依据",
                "rule_version": current_version,
                "rule_status": str(rule.get("status") or ""),
            }
        if ticket.rule_version != current_version:
            return self._reject_stale(
                ticket,
                current_version,
                f"规则 {ticket.rule_no} 已修订（本次选择版本 {ticket.rule_version}，当前版本 {current_version}），请重新反查后再进入交互查询",
            )

        dataset = field_registry.find_dataset_by_id(ticket.dataset_id)
        if dataset is None:
            return self._reject_stale(ticket, current_version, "目标数据集已归档，无法进入交互查询")
        fields = field_registry.ensure_fields(dataset)
        target_field = field_registry.field_by_id(fields, ticket.field_id)
        if target_field is None:
            return self._reject_stale(ticket, current_version, f"字段标识 {ticket.field_id} 已不在数据集字段定义中")
        dataset_version = int(dataset.get("字段版本") or 0)
        if ticket.dataset_code != field_registry.dataset_code(dataset):
            return self._reject_stale(ticket, current_version, "目标数据集编号已调整，请重新反查")
        # 字段改名不改变 field_id，选择仍然有效：按当前名称登记，并把当时的字段版本写进查询依据。

        ticket.consumed = True
        ticket.consumed_at = now_iso()
        values = self._query_values(source, rule, dataset, target_field, ticket, dataset_version)
        entry, missing = self._query_service.create_entry(values)
        if entry is None:
            return {
                "ok": False,
                "code": "query_rejected",
                "message": f"交互查询登记未通过：{'、'.join(missing)}",
            }
        return {
            "ok": True,
            "code": "consumed",
            "message": f"已依据规则 {ticket.rule_no}（版本 {current_version}）登记交互查询",
            "rule_version": current_version,
            "rule_status": str(rule.get("status") or ""),
            "dataset_field_version": dataset_version,
            "field_version_at_selection": ticket.dataset_field_version,
            "field_name": target_field["name"],
            "selection_consumed_at": ticket.consumed_at,
            "query_entry": entry,
        }

    def _query_values(
        self,
        source: RuleSource,
        rule: dict[str, Any],
        dataset: dict[str, Any],
        target_field: dict[str, Any],
        ticket: SelectionTicket,
        dataset_version: int,
    ) -> dict[str, Any]:
        expressions = []
        for key, _label in source.expressions:
            expression = str(rule.get(key) or "").strip()
            if expression:
                expressions.append(expression)
        if not expressions:
            expressions.append(self._direct_expression(rule))

        values: dict[str, Any] = {
            "语句编号": f"QUER-REF-{ticket.rule_no}-{ticket.token[:6]}",
            "查询表达式": " AND ".join(expressions) if expressions else f"SELECT * WHERE {target_field['name']}",
            "目标数据集": field_registry.dataset_code(dataset),
            "返回字段": target_field["name"],
            "排序条件": str(rule.get("排序字段") or ""),
            "最大行数": str(rule.get("输出行数上限") or ""),
            "来源规则编号": ticket.rule_no,
            "来源规则版本": ticket.rule_version,
            "来源字段标识": ticket.field_id,
            "数据集字段版本": dataset_version,
        }
        return values

    @staticmethod
    def _direct_expression(rule: dict[str, Any]) -> str:
        field_name = str(rule.get("筛选字段") or rule.get("分片字段") or rule.get("目标字段") or "")
        operator = str(rule.get("比较运算符") or "")
        value = str(rule.get("比较值") or "")
        if field_name and operator:
            return f"{field_name} {operator} {value}".strip()
        return field_name or "1 = 1"


field_reference_service = FieldReferenceService()
