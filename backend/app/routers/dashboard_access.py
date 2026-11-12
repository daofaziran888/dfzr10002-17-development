"""数据看板权限编排接口：授权发布、范围判定、分享链接、历史访问与审批台账。

接口按“最严优先”裁决冲突，任何越权筛选或只读改动都会返回 ok=false 并给出原因；
真正的状态变更全部落在 app.services.dashboard_access 里。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload
from app.services.dashboard_access import dashboard_access_service

router = APIRouter(prefix="/api/dashboard", tags=["数据看板权限"])


def _panel_or_404(entry_id: int) -> None:
    if dashboard_access_service.chart_visibility(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")


@router.get("/{entry_id}/permissions")
def get_permissions(entry_id: int) -> dict[str, Any]:
    """读取看板授权编排：团队、可见范围、允许维度、继承来源与图表可见状态。"""
    payload = dashboard_access_service.permissions(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return payload


@router.post("/{entry_id}/permissions", response_model=ActionResult)
def create_authorization(entry_id: int, payload: EntryPayload) -> ActionResult:
    """发布面板授权：绑定团队、可见范围、允许维度/图表与继承来源。

    团队主管只能授予本部门；部门负责人可跨部门只读；继承来源不得扩权。
    超出授权范围的维度会被裁剪或拒绝，所有发布都写入审批台账。
    """
    result = dashboard_access_service.create_authorization(entry_id, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry={key: result[key] for key in ("entry", "conflict_resolutions", "conflict_rules")
               if key in result} or None,
    )


@router.post("/{entry_id}/permissions/{identifier}/actions", response_model=ActionResult)
def run_authorization_action(entry_id: int, identifier: str, payload: EntryPayload) -> ActionResult:
    """审批通过、驳回、收紧范围、撤销授权；收紧与撤销必须记录部门负责人审批人。"""
    result = dashboard_access_service.run_authorization_action(entry_id, identifier, payload.values)
    return ActionResult(
        ok=bool(result.get("ok")),
        message=str(result.get("message")),
        entry=result.get("entry") if isinstance(result.get("entry"), dict) else None,
    )


@router.post("/{entry_id}/access/evaluate")
def evaluate_access(entry_id: int, payload: EntryPayload) -> dict[str, Any]:
    """判定某成员对面板的可见维度与参与刷新的图表；越权维度整体拒绝。

    每次判定都会写入访问历史，历史里保留当时授权快照，后续收紧不回写。
    """
    result = dashboard_access_service.evaluate(entry_id, payload.values)
    if result.get("panel") is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return result


@router.get("/{entry_id}/charts/visibility")
def get_chart_visibility(entry_id: int) -> dict[str, Any]:
    """图表配置清单随看板授权更新可见状态：已授权可见、未授权。"""
    payload = dashboard_access_service.chart_visibility(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return payload


@router.get("/{entry_id}/shares")
def list_shares(entry_id: int) -> dict[str, Any]:
    """读取分享链接清单；链接均为只读快照，不改动面板权限。"""
    rows = dashboard_access_service.shares(entry_id)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return {"items": rows, "total": len(rows), "read_only": True}


@router.post("/{entry_id}/shares", response_model=ActionResult)
def create_share(entry_id: int, payload: EntryPayload) -> ActionResult:
    """创建只读分享链接：团队主管仅限本部门，部门负责人可跨部门只读。"""
    result = dashboard_access_service.create_share(entry_id, payload.values)
    entry = result.get("entry")
    entry = {**entry, "link": result["link"]} if isinstance(entry, dict) and "link" in result else entry
    return ActionResult(ok=bool(result.get("ok")), message=str(result.get("message")), entry=entry)


@router.post("/{entry_id}/shares/{token}")
def share_permission_change(entry_id: int, token: str, payload: EntryPayload) -> dict[str, Any]:
    """分享链接不能改动面板权限：任何写操作都按 403 拒绝。"""
    result = dashboard_access_service.share_permission_change(entry_id, token)
    raise HTTPException(status_code=403, detail=str(result.get("message")))


@router.get("/{entry_id}/access-history")
def get_access_history(entry_id: int) -> dict[str, Any]:
    """历史访问清单：保留原授权快照，包含请求维度、结果与拒绝原因。"""
    payload = dashboard_access_service.access_history(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return payload


@router.get("/{entry_id}/approvals")
def get_approvals(entry_id: int) -> dict[str, Any]:
    """审批台账：每次范围变化都记录团队、继承来源、审批人和变更前后维度。"""
    payload = dashboard_access_service.approvals(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"看板面板 {entry_id} 不存在或已归档")
    return payload
