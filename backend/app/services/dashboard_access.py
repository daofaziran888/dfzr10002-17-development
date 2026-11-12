"""数据看板权限编排：团队授权、继承、分享链接、访问历史与审批台账。

设计要点（冲突裁决规则，按“最严优先”执行）：

1. 同一团队存在多条生效授权时，有效维度/图表取所有授权范围的**交集**；
2. 直接授权与继承来源冲突时同样取交集，继承来源只能缩小范围、不得扩权；
3. 只读与可写冲突时只读优先：任一来源为只读（本部门只读、跨部门只读、角色只读、
   分享链接）即降为只读；
4. 分享链接的有效范围 = 创建时快照 ∩ 当前授权范围，链接永远不会因新授权扩容；
5. 授权发布、收紧、撤销都以版本号推进，并追加审批台账，保留团队、继承来源和审批人；
6. 继承来源被撤销或收紧后，下游授权的有效范围同步失效/收缩，图表可见状态与刷新范围
   一并重算；历史访问记录保留访问当时的授权快照，不回写。

越权筛选（请求了授权范围外的维度）会被整体拒绝；图表则只让授权范围内的参与刷新。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

PANEL_TABLE = "dashboard"
TEAM_TABLE = "team"
MEMBER_TABLE = "member"
AUTH_TABLE = "dashboard_authorization"
SHARE_TABLE = "dashboard_share"
HISTORY_TABLE = "dashboard_access_history"
APPROVAL_TABLE = "dashboard_approval"
CHART_TABLE = "chart"

AUTH_ACTIVE = "已发布"
AUTH_APPROVED = "已通过"
READONLY_SCOPES = {"本部门只读", "跨部门只读"}
VISIBLE_SCOPES = {"本部门", "本部门只读", "跨部门只读"}
ROLE_READONLY = "只读"
ROLE_MEMBER = "成员"
ROLE_SUPERVISOR = "团队主管"
ROLE_DEPT_HEAD = "部门负责人"

CONFLICT_RULES = [
    "同一团队多条生效授权取交集（最严优先）",
    "直接授权与继承来源取交集，继承只可缩小、不可扩权",
    "只读来源优先：本部门只读、跨部门只读、只读角色、分享链接均不可写",
    "分享链接范围 = 创建快照 ∩ 当前授权，永不因新授权扩容",
    "继承来源撤销后下游授权同步失效，历史访问快照不回写",
]

DEFAULT_ACTIONS = {"view", "查看", ""}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[、,，;\s]+", str(value)) if item.strip()]


def _intersect(sets: list[set[str]]) -> set[str]:
    if not sets:
        return set()
    result = set(sets[0])
    for item in sets[1:]:
        result &= item
    return result


def _member_id(member: dict[str, Any]) -> int:
    return int(member.get("id", 0))


class DashboardAccessService:
    def __init__(self) -> None:
        self.conflict_rules = list(CONFLICT_RULES)
        self.recompute_all_charts()

    # ------------------------------------------------------------------ 基础查询

    def _panel(self, entry_id: int | None = None, code: str | None = None) -> dict[str, Any] | None:
        if entry_id is not None:
            return store.find(PANEL_TABLE, entry_id)
        if code:
            for row in store.rows(PANEL_TABLE):
                if str(row.get("面板编号")) == code:
                    return row
        return None

    def _members(self) -> list[dict[str, Any]]:
        return store.rows(MEMBER_TABLE)

    def _teams(self) -> list[dict[str, Any]]:
        return store.rows(TEAM_TABLE)

    def _charts(self) -> list[dict[str, Any]]:
        return store.rows(CHART_TABLE)

    def _auth_rows(self, panel_code: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(AUTH_TABLE)
        if panel_code:
            rows = [row for row in rows if str(row.get("面板编号")) == panel_code]
        return rows

    def resolve_member(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        text = str(value).strip()
        for member in self._members():
            if (
                str(member.get("姓名")) == text
                or str(member.get("成员编号")) == text
                or str(member.get("id")) == text
            ):
                return member
        return None

    def resolve_team(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        text = str(value).strip()
        for team in self._teams():
            if (
                str(team.get("团队名称")) == text
                or str(team.get("团队编号")) == text
                or str(team.get("id")) == text
            ):
                return team
        return None

    def _find_auth(self, panel_code: str, identifier: Any) -> dict[str, Any] | None:
        text = str(identifier).strip()
        for auth in self._auth_rows(panel_code):
            if str(auth.get("授权编号")) == text or str(auth.get("id")) == text:
                return auth
        return None

    def panel_chart_codes(self, panel: dict[str, Any]) -> list[str]:
        codes = _as_list(panel.get("图表集合"))
        known = {str(row.get("图表编号")) for row in self._charts()}
        return [code for code in codes if code in known]

    def _chart_rows(self, panel: dict[str, Any]) -> list[dict[str, Any]]:
        codes = set(self.panel_chart_codes(panel))
        return [row for row in self._charts() if str(row.get("图表编号")) in codes]

    # ------------------------------------------------------------------ 继承与有效范围

    def _scope(self, auth: dict[str, Any], stack: list[str] | None = None) -> dict[str, Any]:
        """计算单条授权的有效范围；继承来源不生效时整条授权失效。"""
        stack = list(stack or [])
        code = str(auth.get("授权编号"))
        if code in stack:
            return {"active": False, "reason": f"继承链出现循环：{code}", "dimensions": set(),
                    "charts": set(), "chain": [], "flat_reason": "继承循环"}
        if str(auth.get("状态")) != AUTH_ACTIVE or str(auth.get("审批状态")) != AUTH_APPROVED:
            reason = "授权已撤销" if str(auth.get("状态")) == "已撤销" else "授权尚未审批通过"
            return {"active": False, "reason": reason, "dimensions": set(), "charts": set(),
                    "chain": [code], "flat_reason": reason}

        dimensions = set(_as_list(auth.get("允许维度")))
        charts = set(_as_list(auth.get("允许图表")))
        chain = [code]
        parent_code = auth.get("继承来源")
        parent_note = ""
        if parent_code:
            parent = self._find_auth(str(auth.get("面板编号")), parent_code)
            if parent is None:
                return {"active": False, "reason": f"继承来源 {parent_code} 不存在",
                        "dimensions": set(), "charts": set(), "chain": [code],
                        "flat_reason": f"继承来源 {parent_code} 不存在"}
            if str(parent.get("面板编号")) != str(auth.get("面板编号")):
                return {"active": False, "reason": f"继承来源 {parent_code} 不属于同一面板",
                        "dimensions": set(), "charts": set(), "chain": [code],
                        "flat_reason": f"继承来源 {parent_code} 不属于同一面板"}
            parent_scope = self._scope(parent, stack + [code])
            if not parent_scope["active"]:
                reason = f"继承来源 {parent_code} 已失效：{parent_scope['reason']}"
                return {"active": False, "reason": reason, "dimensions": set(), "charts": set(),
                        "chain": [code] + parent_scope["chain"], "flat_reason": reason}
            dimensions &= parent_scope["dimensions"]
            charts &= parent_scope["charts"]
            chain += parent_scope["chain"]
            parent_note = f"继承 {parent_code} 后取交集"

        if not dimensions and not charts:
            return {"active": False, "reason": "授权范围为空白", "dimensions": set(),
                    "charts": set(), "chain": chain, "flat_reason": "授权范围为空白"}
        return {
            "active": True,
            "reason": parent_note or None,
            "dimensions": dimensions,
            "charts": charts,
            "chain": chain,
            "flat_reason": parent_note or None,
        }

    def _inherits_from(self, auth: dict[str, Any], ancestor_code: str) -> bool:
        panel_code = str(auth.get("面板编号"))
        parent_code = auth.get("继承来源")
        seen: set[str] = set()
        while parent_code:
            text = str(parent_code)
            if text in seen:
                return False
            seen.add(text)
            if text == ancestor_code:
                return True
            parent = self._find_auth(panel_code, text)
            if parent is None:
                return False
            parent_code = parent.get("继承来源")
        return False

    def _team_scope(self, panel_code: str, team: str) -> dict[str, Any]:
        """同一团队所有生效授权取交集，附带冲突裁决说明。"""
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for auth in self._auth_rows(panel_code):
            if str(auth.get("团队")) != team:
                continue
            scope = self._scope(auth)
            if scope["active"]:
                candidates.append((auth, scope))

        resolutions: list[dict[str, Any]] = []
        if not candidates:
            return {"active": False, "dimensions": set(), "charts": set(), "candidates": [],
                    "resolutions": resolutions, "reason": "当前团队没有已审批生效的授权",
                    "chain": [], "read_only": False}

        dimensions = _intersect([scope["dimensions"] for _, scope in candidates])
        charts = _intersect([scope["charts"] for _, scope in candidates])
        if len(candidates) > 1:
            resolutions.append({
                "规则": CONFLICT_RULES[0],
                "来源": [str(auth.get("授权编号")) for auth, _ in candidates],
                "结果维度": sorted(dimensions),
                "结果图表": sorted(charts),
            })
        for auth, scope in candidates:
            if auth.get("继承来源"):
                resolutions.append({
                    "规则": CONFLICT_RULES[1],
                    "来源": [str(auth.get("授权编号")), str(auth.get("继承来源"))],
                    "结果维度": sorted(scope["dimensions"]),
                    "结果图表": sorted(scope["charts"]),
                })

        readonly = any(str(auth.get("可见范围")) in READONLY_SCOPES for auth, _ in candidates)
        if readonly:
            resolutions.append({"规则": CONFLICT_RULES[2],
                                "来源": [str(auth.get("授权编号")) for auth, _ in candidates],
                                "结果维度": sorted(dimensions), "结果图表": sorted(charts)})

        if not dimensions and not charts:
            return {"active": False, "dimensions": set(), "charts": set(),
                    "candidates": candidates, "resolutions": resolutions,
                    "reason": "授权范围取交集后为空", "chain": [], "read_only": readonly}

        chain: list[str] = []
        for auth, scope in candidates:
            for code in scope["chain"]:
                if code not in chain:
                    chain.append(code)
        return {
            "active": True,
            "dimensions": dimensions,
            "charts": charts,
            "candidates": candidates,
            "resolutions": resolutions,
            "reason": None,
            "chain": chain,
            "read_only": readonly,
        }

    # ------------------------------------------------------------------ 快照与历史

    def _snapshot(self, candidates: list[tuple[dict[str, Any], dict[str, Any]]],
                  dimensions: set[str], charts: set[str],
                  share_token: str | None = None) -> dict[str, list[str] | str | None]:
        codes: list[str] = []
        inherit: list[str] = []
        approvers: list[str] = []
        for auth, _scope in candidates:
            code = str(auth.get("授权编号"))
            if code not in codes:
                codes.append(code)
            parent = auth.get("继承来源")
            if parent and str(parent) not in inherit:
                inherit.append(str(parent))
            approver = auth.get("审批人")
            if approver and str(approver) not in approvers:
                approvers.append(str(approver))
        return {
            "允许维度": sorted(dimensions),
            "允许图表": sorted(charts),
            "授权编号": codes,
            "继承来源": inherit,
            "审批人": approvers,
            "分享链接": share_token,
        }

    def _record_history(
        self,
        *,
        panel: dict[str, Any] | None,
        member: dict[str, Any] | None,
        member_value: Any,
        role: str,
        team: str,
        access_type: str,
        requested_dimensions: list[str],
        snapshot: dict[str, Any],
        allowed: bool,
        reason: str,
        share_token: str | None,
    ) -> dict[str, Any]:
        rows = store.rows(HISTORY_TABLE)
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = {
            "id": new_id,
            "访问编号": f"ACC-{new_id:04d}",
            "面板编号": str(panel.get("面板编号")) if panel else None,
            "访问人": str(member.get("姓名")) if member else str(member_value or "未知"),
            "团队": team,
            "角色": role,
            "访问类型": access_type or "查看",
            "访问时间": _now(),
            "请求维度": requested_dimensions,
            "授权快照": {
                "允许维度": sorted(_as_list(snapshot.get("允许维度"))),
                "允许图表": sorted(_as_list(snapshot.get("允许图表"))),
                "授权编号": list(snapshot.get("授权编号") or []),
                "继承来源": list(snapshot.get("继承来源") or []),
                "审批人": list(snapshot.get("审批人") or []),
                "分享链接": snapshot.get("分享链接"),
                "结果": "允许" if allowed else "拒绝",
                "拒绝原因": None if allowed else reason,
            },
            "结果": "允许" if allowed else "拒绝",
            "拒绝原因": None if allowed else reason,
            "分享链接": share_token,
        }
        rows.append(entry)
        return entry

    # ------------------------------------------------------------------ 访问判定

    def evaluate(
        self,
        entry_id: int,
        payload: dict[str, Any],
        *,
        record: bool = True,
    ) -> dict[str, Any]:
        panel = self._panel(entry_id)
        member_value = payload.get("成员") or payload.get("访问人") or payload.get("member")
        member = self.resolve_member(member_value)
        requested_dimensions = _as_list(payload.get("请求维度") or payload.get("dimensions"))
        requested_charts = _as_list(payload.get("请求图表") or payload.get("charts"))
        share_token = payload.get("分享令牌") or payload.get("share_token")
        action = str(payload.get("动作") or payload.get("action") or "view").strip()

        role = str(member.get("角色")) if member else "未知"
        team = str(member.get("团队名称")) if member else ""
        base: dict[str, Any] = {
            "allowed": False,
            "reason": "",
            "panel": {"id": panel.get("id"), "面板编号": panel.get("面板编号"),
                      "面板名称": panel.get("面板名称")} if panel else None,
            "member": {"姓名": member.get("姓名"), "成员编号": member.get("成员编号"),
                       "团队": team, "角色": role} if member else None,
            "team": team,
            "role": role,
            "read_only": role == ROLE_READONLY,
            "visible_scope": "",
            "dimensions": [],
            "charts_to_refresh": [],
            "excluded_charts": [],
            "refresh_allowed": False,
            "requested_dimensions": requested_dimensions,
            "denied_dimensions": [],
            "conflict_resolutions": [],
            "inheritance_chain": [],
            "authorizations": [],
            "share": None,
            "history_record": None,
        }

        if panel is None:
            base["reason"] = f"看板面板 {entry_id} 不存在或已归档"
            return base
        if member is None:
            base["reason"] = f"成员 {member_value} 不存在，无法判定权限"
            if record:
                base["history_record"] = self._record_history(
                    panel=panel, member=None, member_value=member_value, role=role, team=team,
                    access_type=action, requested_dimensions=requested_dimensions,
                    snapshot={}, allowed=False, reason=base["reason"], share_token=share_token,
                )
            return base

        if share_token:
            result = self._evaluate_share(panel, member, payload, share_token, action,
                                          requested_dimensions, requested_charts)
        else:
            result = self._evaluate_grant(panel, member, action, requested_dimensions,
                                          requested_charts)

        base.update(result)
        if record:
            base["history_record"] = self._record_history(
                panel=panel, member=member, member_value=member_value,
                role=role, team=team, access_type=action,
                requested_dimensions=requested_dimensions,
                snapshot=result.get("snapshot", {}),
                allowed=bool(base["allowed"]), reason=str(base["reason"]),
                share_token=share_token,
            )
        return base

    def _evaluate_share(
        self,
        panel: dict[str, Any],
        member: dict[str, Any],
        payload: dict[str, Any],
        share_token: Any,
        action: str,
        requested_dimensions: list[str],
        requested_charts: list[str],
    ) -> dict[str, Any]:
        share = None
        for row in store.rows(SHARE_TABLE):
            if str(row.get("分享令牌")) == str(share_token) or str(row.get("分享编号")) == str(share_token):
                share = row
                break
        if share is None:
            return {"allowed": False, "reason": f"分享链接 {share_token} 不存在", "share": None,
                    "snapshot": {}}
        if str(share.get("面板编号")) != str(panel.get("面板编号")):
            return {"allowed": False, "reason": "分享链接与面板不匹配", "snapshot": {}}
        if str(share.get("状态")) != "有效":
            return {"allowed": False, "reason": "分享链接已失效", "snapshot": {}}
        if str(member.get("团队名称")) != str(share.get("目标团队")):
            return {"allowed": False,
                    "reason": f"分享链接仅限{share.get('目标团队')}使用，当前团队越权",
                    "snapshot": {}}

        auth = self._find_auth(str(panel.get("面板编号")), share.get("授权编号"))
        if auth is None:
            return {"allowed": False, "reason": "分享链接所属授权不存在", "snapshot": {}}
        scope = self._scope(auth)
        if not scope["active"]:
            return {"allowed": False,
                    "reason": f"分享链接所属授权已失效：{scope['reason']}", "snapshot": {}}

        snapshot_dims = set(_as_list(share.get("快照维度")))
        snapshot_charts = set(_as_list(share.get("快照图表")))
        dimensions = snapshot_dims & scope["dimensions"]
        charts = snapshot_charts & scope["charts"]
        read_only = True
        share_info = {
            "分享令牌": str(share.get("分享令牌")),
            "目标团队": str(share.get("目标团队")),
            "可见范围": str(share.get("可见范围")),
            "快照维度": sorted(snapshot_dims),
            "快照图表": sorted(snapshot_charts),
            "read_only": True,
        }
        resolutions = [{
            "规则": CONFLICT_RULES[3],
            "来源": [str(auth.get("授权编号")), str(share.get("分享令牌"))],
            "结果维度": sorted(dimensions),
            "结果图表": sorted(charts),
        }]

        if action not in DEFAULT_ACTIONS:
            return {"allowed": False, "reason": "共享链接为只读，不能改动面板权限",
                    "read_only": True, "share": share_info,
                    "dimensions": sorted(dimensions), "charts_to_refresh": sorted(charts),
                    "refresh_allowed": False,
                    "conflict_resolutions": resolutions,
                    "inheritance_chain": scope["chain"],
                    "authorizations": [str(auth.get("授权编号"))],
                    "snapshot": self._snapshot([(auth, scope)], dimensions, charts,
                                               str(share.get("分享令牌"))),
                    "visible_scope": "只读分享"}

        denied = sorted(set(requested_dimensions) - dimensions)
        if denied:
            reason = f"请求维度{'、'.join(denied)}不在分享快照范围，越权筛选已拒绝"
            return {"allowed": False, "reason": reason, "read_only": True, "share": share_info,
                    "dimensions": sorted(dimensions), "charts_to_refresh": [],
                    "refresh_allowed": False, "denied_dimensions": denied,
                    "conflict_resolutions": resolutions, "inheritance_chain": scope["chain"],
                    "authorizations": [str(auth.get("授权编号"))],
                    "snapshot": self._snapshot([(auth, scope)], dimensions, charts,
                                               str(share.get("分享令牌"))),
                    "visible_scope": "只读分享"}

        return self._finish(
            panel=panel,
            allowed=True, reason="允许访问分享快照范围内的维度与图表",
            dimensions=dimensions, charts=charts, requested_charts=requested_charts,
            read_only=read_only, visible_scope="只读分享",
            resolutions=resolutions, chain=scope["chain"],
            auth_codes=[str(auth.get("授权编号"))],
            candidates=[(auth, scope)], share=share_info,
        )

    def _evaluate_grant(
        self,
        panel: dict[str, Any],
        member: dict[str, Any],
        action: str,
        requested_dimensions: list[str],
        requested_charts: list[str],
    ) -> dict[str, Any]:
        role = str(member.get("角色"))
        team = str(member.get("团队名称"))
        if role == ROLE_READONLY and action not in DEFAULT_ACTIONS:
            return {"allowed": False, "reason": "只读角色只能查看，不能改动面板权限",
                    "read_only": True}

        panel_code = str(panel.get("面板编号"))
        combined = self._team_scope(panel_code, team)
        candidates = combined["candidates"]
        dimensions: set[str] = combined["dimensions"]
        charts: set[str] = combined["charts"]
        resolutions: list[dict[str, Any]] = list(combined["resolutions"])
        read_only = bool(combined["read_only"]) or role == ROLE_READONLY

        if not combined["active"]:
            return {"allowed": False, "reason": combined["reason"],
                    "read_only": read_only, "dimensions": sorted(dimensions),
                    "charts_to_refresh": [], "refresh_allowed": False,
                    "conflict_resolutions": resolutions,
                    "inheritance_chain": combined["chain"], "authorizations": [],
                    "snapshot": self._snapshot(candidates, dimensions, charts)}

        if read_only and action not in DEFAULT_ACTIONS:
            return {"allowed": False, "reason": "当前授权为只读，不能改动面板权限",
                    "read_only": True, "dimensions": sorted(dimensions),
                    "charts_to_refresh": sorted(charts), "refresh_allowed": False,
                    "conflict_resolutions": resolutions,
                    "inheritance_chain": combined["chain"],
                    "authorizations": [str(auth.get("授权编号")) for auth, _ in candidates],
                    "snapshot": self._snapshot(candidates, dimensions, charts)}

        denied = sorted(set(requested_dimensions) - dimensions)
        if denied:
            reason = f"请求维度{'、'.join(denied)}不在授权范围，越权筛选已拒绝"
            return {"allowed": False, "reason": reason, "read_only": read_only,
                    "dimensions": sorted(dimensions), "charts_to_refresh": [],
                    "refresh_allowed": False, "denied_dimensions": denied,
                    "conflict_resolutions": resolutions,
                    "inheritance_chain": combined["chain"],
                    "authorizations": [str(auth.get("授权编号")) for auth, _ in candidates],
                    "snapshot": self._snapshot(candidates, dimensions, charts),
                    "visible_scope": "只读" if read_only else "、".join(
                        sorted({str(auth.get("可见范围")) for auth, _ in candidates}))}

        scope_label = "只读" if read_only else "、".join(
            sorted({str(auth.get("可见范围")) for auth, _ in candidates}))
        return self._finish(
            panel=panel,
            allowed=True, reason="允许访问授权范围内的维度与图表",
            dimensions=dimensions, charts=charts, requested_charts=requested_charts,
            read_only=read_only, visible_scope=scope_label,
            resolutions=resolutions, chain=combined["chain"],
            auth_codes=[str(auth.get("授权编号")) for auth, _ in candidates],
            candidates=candidates,
        )

    def _finish(
        self,
        *,
        panel: dict[str, Any],
        allowed: bool,
        reason: str,
        dimensions: set[str],
        charts: set[str],
        requested_charts: list[str],
        read_only: bool,
        visible_scope: str,
        resolutions: list[dict[str, Any]],
        chain: list[str],
        auth_codes: list[str],
        candidates: list[tuple[dict[str, Any], dict[str, Any]]],
        share: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """图表只保留授权范围内的参与刷新；未授权图表不阻断维度查看。"""
        panel_chart_set = set(self.panel_chart_codes(panel))
        effective_charts = charts & panel_chart_set if panel_chart_set else set(charts)
        requested = set(requested_charts)
        if requested:
            charts_to_refresh = sorted(requested & effective_charts)
            excluded = sorted(requested - effective_charts)
        else:
            charts_to_refresh = sorted(effective_charts)
            excluded = []
        snapshot = self._snapshot(candidates, dimensions, charts,
                                 str(share.get("分享令牌")) if share else None)
        return {
            "allowed": allowed,
            "reason": reason,
            "read_only": read_only,
            "visible_scope": visible_scope,
            "dimensions": sorted(dimensions),
            "charts_to_refresh": charts_to_refresh,
            "excluded_charts": excluded,
            "refresh_allowed": bool(charts_to_refresh),
            "denied_dimensions": [],
            "conflict_resolutions": resolutions,
            "inheritance_chain": chain,
            "authorizations": auth_codes,
            "share": share,
            "snapshot": snapshot,
        }

    # ------------------------------------------------------------------ 审批台账

    def _append_approval(
        self,
        *,
        panel_code: str,
        auth: dict[str, Any],
        change_type: str,
        before: list[str],
        after: list[str],
        operator: str,
        approver: str | None,
        result: str,
        remark: str,
    ) -> dict[str, Any]:
        rows = store.rows(APPROVAL_TABLE)
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = {
            "id": new_id,
            "台账编号": f"APRV-{new_id:04d}",
            "面板编号": panel_code,
            "授权编号": str(auth.get("授权编号")),
            "变更类型": change_type,
            "团队": str(auth.get("团队")),
            "继承来源": auth.get("继承来源"),
            "变更前维度": sorted(before),
            "变更后维度": sorted(after),
            "操作人": operator,
            "审批人": approver,
            "审批结果": result,
            "变更时间": _now(),
            "备注": remark,
        }
        rows.append(entry)
        return entry

    # ------------------------------------------------------------------ 图表可见状态

    def recompute_chart_visibility(self, panel: dict[str, Any]) -> list[dict[str, Any]]:
        """按面板当前生效授权重算图表配置的可见状态。"""
        panel_code = str(panel.get("面板编号"))
        panel_charts = set(self.panel_chart_codes(panel))
        per_chart: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for auth in self._auth_rows(panel_code):
            scope = self._scope(auth)
            if not scope["active"]:
                continue
            for chart_code in scope["charts"]:
                if chart_code in panel_charts:
                    per_chart.setdefault(chart_code, []).append((auth, scope))

        items: list[dict[str, Any]] = []
        for row in self._charts():
            code = str(row.get("图表编号"))
            if code not in panel_charts:
                continue
            authorized = per_chart.get(code, [])
            if authorized:
                teams = sorted({str(auth.get("团队")) for auth, _ in authorized})
                dims = sorted(_intersect([scope["dimensions"] for _, scope in authorized]))
                status = "已授权可见"
            else:
                teams, dims = [], []
                status = "未授权"
            row["可见状态"] = status
            row["可见团队"] = "、".join(teams) if teams else "—"
            row["可见维度"] = "、".join(dims) if dims else "—"
            row["关联面板"] = panel_code
            row["可见更新时间"] = _now()
            items.append({
                "图表编号": code,
                "图表名称": row.get("图表名称"),
                "可见状态": status,
                "可见团队": row["可见团队"],
                "可见维度": row["可见维度"],
                "关联面板": panel_code,
                "授权编号": [str(auth.get("授权编号")) for auth, _ in authorized],
            })
        return items

    def recompute_all_charts(self) -> None:
        for panel in store.rows(PANEL_TABLE):
            try:
                self.recompute_chart_visibility(panel)
            except Exception:
                # 示例数据里的泛化面板没有真实图表编号，忽略即可。
                continue

    # ------------------------------------------------------------------ 授权编排

    def _authorization_conflicts(self, panel_code: str, team: str,
                                 dimensions: set[str], charts: set[str]) -> list[dict[str, Any]]:
        resolutions: list[dict[str, Any]] = []
        existing = [auth for auth in self._auth_rows(panel_code)
                    if str(auth.get("团队")) == team and self._scope(auth)["active"]]
        if existing:
            combined = self._team_scope(panel_code, team)
            resolutions.append({
                "规则": CONFLICT_RULES[0],
                "来源": [str(auth.get("授权编号")) for auth in existing],
                "结果维度": sorted(combined["dimensions"] & dimensions),
                "结果图表": sorted(combined["charts"] & charts),
                "说明": "新授权发布后与同团队生效授权取交集，最严范围生效",
            })
        return resolutions

    def create_authorization(self, entry_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        panel = self._panel(entry_id)
        if panel is None:
            return {"ok": False, "message": f"看板面板 {entry_id} 不存在或已归档"}
        if payload.get("分享令牌") or payload.get("share_token"):
            return {"ok": False, "message": "共享链接为只读，不能改动面板权限"}

        operator = self.resolve_member(payload.get("操作人") or payload.get("operator"))
        if operator is None:
            return {"ok": False, "message": "操作人不存在，无法登记授权"}
        role = str(operator.get("角色"))
        if role in (ROLE_READONLY, ROLE_MEMBER):
            return {"ok": False, "message": f"{role}角色不能发布或调整看板授权"}

        team = self.resolve_team(payload.get("团队") or payload.get("team"))
        if team is None:
            return {"ok": False, "message": "团队不存在，请使用团队名称或团队编号"}
        team_name = str(team.get("团队名称"))
        operator_team = str(operator.get("团队名称"))
        scope = str(payload.get("可见范围") or payload.get("visible_scope") or "").strip()
        if scope not in VISIBLE_SCOPES:
            return {"ok": False, "message": f"可见范围「{scope}」不合法，可选：{'、'.join(sorted(VISIBLE_SCOPES))}"}

        if role == ROLE_SUPERVISOR:
            if team_name != operator_team:
                return {"ok": False, "message": "团队主管只能把面板授权给本部门，跨部门需部门负责人审批"}
            if scope not in {"本部门", "本部门只读"}:
                return {"ok": False, "message": "团队主管只能授予本部门范围，跨部门只读需部门负责人操作"}
        elif role == ROLE_DEPT_HEAD:
            if team_name != operator_team and scope != "跨部门只读":
                return {"ok": False, "message": "部门负责人跨部门授权只允许只读范围"}
        else:
            return {"ok": False, "message": f"角色{role}没有授权编排权限"}

        dimensions = set(_as_list(payload.get("允许维度") or payload.get("dimensions")))
        charts = set(_as_list(payload.get("允许图表") or payload.get("charts")))
        panel_charts = set(self.panel_chart_codes(panel))
        unknown_charts = charts - panel_charts
        if unknown_charts:
            return {"ok": False,
                    "message": f"图表{'、'.join(sorted(unknown_charts))}不属于面板{panel.get('面板编号')}"}
        if not dimensions:
            return {"ok": False, "message": "允许维度不能为空，越权筛选将无法复核"}
        if not charts:
            return {"ok": False, "message": "允许图表不能为空，授权范围内至少包含一个图表"}

        parent_code = payload.get("继承来源") or payload.get("inherited_from")
        parent = None
        resolutions: list[dict[str, Any]] = []
        if parent_code:
            parent = self._find_auth(str(panel.get("面板编号")), parent_code)
            if parent is None:
                return {"ok": False, "message": f"继承来源 {parent_code} 不存在"}
            parent_scope = self._scope(parent)
            if not parent_scope["active"]:
                return {"ok": False,
                        "message": f"继承来源 {parent_code} 未生效或已撤销：{parent_scope['reason']}"}
            clipped_dims = dimensions - parent_scope["dimensions"]
            clipped_charts = charts - parent_scope["charts"]
            if clipped_dims or clipped_charts:
                resolutions.append({
                    "规则": CONFLICT_RULES[1],
                    "来源": [str(parent.get("授权编号"))],
                    "说明": "继承来源不得扩权，超出部分已按父授权裁剪",
                    "裁剪维度": sorted(clipped_dims),
                    "裁剪图表": sorted(clipped_charts),
                })
                dimensions &= parent_scope["dimensions"]
                charts &= parent_scope["charts"]
            if not dimensions or not charts:
                return {"ok": False, "message": "继承来源取交集后授权范围为空"}

        approvals = self._authorization_conflicts(str(panel.get("面板编号")), team_name,
                                                  dimensions, charts)
        resolutions.extend(approvals)

        approver = self.resolve_member(payload.get("审批人") or payload.get("approver"))
        if approver is not None and str(approver.get("角色")) != ROLE_DEPT_HEAD:
            return {"ok": False, "message": "审批人必须是部门负责人，授权收紧需可追溯"}
        approver_name = str(approver.get("姓名")) if approver else None

        published = approver is not None
        auth_rows = store.rows(AUTH_TABLE)
        new_id = max((int(row.get("id", 0)) for row in auth_rows), default=0) + 1
        auth = {
            "id": new_id,
            "授权编号": f"AUTH-{new_id:04d}",
            "面板编号": str(panel.get("面板编号")),
            "团队": team_name,
            "可见范围": scope,
            "允许维度": sorted(dimensions),
            "允许图表": sorted(charts),
            "继承来源": str(parent.get("授权编号")) if parent else None,
            "状态": "已发布" if published else "待审批",
            "审批状态": AUTH_APPROVED if published else "待审批",
            "操作人": str(operator.get("姓名")),
            "审批人": approver_name,
            "创建时间": _now(),
            "版本": 1,
        }
        auth_rows.append(auth)
        change_type = "发布授权" if published else "申请发布"
        result = "通过" if published else "待审批"
        self._append_approval(
            panel_code=str(panel.get("面板编号")), auth=auth, change_type=change_type,
            before=[], after=sorted(dimensions), operator=str(operator.get("姓名")),
            approver=approver_name, result=result,
            remark=("发布授权，继承来源" + str(auth["继承来源"])) if auth["继承来源"] else "发布看板团队授权",
        )
        if published:
            self.recompute_chart_visibility(panel)
        message = "授权已发布" if published else "授权申请已提交，等待部门负责人审批"
        return {"ok": True, "message": message, "entry": auth,
                "conflict_resolutions": resolutions, "conflict_rules": self.conflict_rules}

    def run_authorization_action(self, entry_id: int, identifier: Any,
                                 payload: dict[str, Any]) -> dict[str, Any]:
        panel = self._panel(entry_id)
        if panel is None:
            return {"ok": False, "message": f"看板面板 {entry_id} 不存在或已归档"}
        if payload.get("分享令牌") or payload.get("share_token"):
            return {"ok": False, "message": "共享链接为只读，不能改动面板权限"}
        auth = self._find_auth(str(panel.get("面板编号")), identifier)
        if auth is None:
            return {"ok": False, "message": f"授权 {identifier} 不存在"}

        action = str(payload.get("action") or payload.get("动作") or "").strip()
        operator = self.resolve_member(payload.get("操作人") or payload.get("operator"))
        approver = self.resolve_member(payload.get("审批人") or payload.get("approver"))
        if operator is None:
            return {"ok": False, "message": "操作人不存在，无法执行授权动作"}
        if str(operator.get("角色")) in (ROLE_READONLY, ROLE_MEMBER):
            return {"ok": False, "message": "只读或成员角色不能改动面板授权"}
        if str(operator.get("角色")) == ROLE_SUPERVISOR and \
                str(operator.get("团队名称")) != str(auth.get("团队")):
            return {"ok": False, "message": "团队主管只能调整本部门授权"}

        before_dims = _as_list(auth.get("允许维度"))
        before_charts = _as_list(auth.get("允许图表"))
        panel_code = str(panel.get("面板编号"))

        if action in ("审批通过", "驳回"):
            if str(auth.get("状态")) != "待审批":
                return {"ok": False, "message": f"授权 {auth.get('授权编号')} 不处于待审批状态"}
            if approver is None or str(approver.get("角色")) != ROLE_DEPT_HEAD:
                return {"ok": False, "message": "审批通过或驳回必须由部门负责人操作"}
            auth["审批人"] = str(approver.get("姓名"))
            if action == "审批通过":
                auth["状态"] = "已发布"
                auth["审批状态"] = AUTH_APPROVED
                self._append_approval(
                    panel_code=panel_code, auth=auth, change_type="审批通过",
                    before=[], after=before_dims, operator=str(operator.get("姓名")),
                    approver=str(approver.get("姓名")), result="通过", remark="部门负责人审批通过")
                self.recompute_chart_visibility(panel)
                return {"ok": True, "message": "授权已审批通过并生效", "entry": auth}
            auth["状态"] = "已驳回"
            auth["审批状态"] = "已驳回"
            self._append_approval(
                panel_code=panel_code, auth=auth, change_type="驳回",
                before=[], after=before_dims, operator=str(operator.get("姓名")),
                approver=str(approver.get("姓名")), result="驳回", remark="部门负责人驳回授权申请")
            return {"ok": True, "message": "授权申请已驳回", "entry": auth}

        if action == "收紧范围":
            if str(auth.get("状态")) != AUTH_ACTIVE:
                return {"ok": False, "message": "只有已发布授权可以收紧范围"}
            if approver is None or str(approver.get("角色")) != ROLE_DEPT_HEAD:
                return {"ok": False, "message": "收紧范围必须记录部门负责人作为审批人"}
            new_dims = set(_as_list(payload.get("允许维度") or payload.get("dimensions")))
            new_charts = set(_as_list(payload.get("允许图表") or payload.get("charts")))
            if not new_dims and not new_charts:
                return {"ok": False, "message": "收紧范围需要提供新的允许维度或允许图表"}
            if not new_dims:
                new_dims = set(before_dims)
            if not new_charts:
                new_charts = set(before_charts)
            if not new_dims <= set(before_dims) or not new_charts <= set(before_charts):
                return {"ok": False, "message": "收紧范围只能缩小维度或图表，扩权需重新走发布审批"}
            if new_dims == set(before_dims) and new_charts == set(before_charts):
                return {"ok": False, "message": "范围没有变化，无需收紧"}
            dependents_before = self._dependent_scopes(panel_code, str(auth.get("授权编号")))
            auth["允许维度"] = sorted(new_dims)
            auth["允许图表"] = sorted(new_charts)
            auth["版本"] = int(auth.get("版本", 1)) + 1
            auth["审批人"] = str(approver.get("姓名"))
            auth["操作人"] = str(operator.get("姓名"))
            self._append_approval(
                panel_code=panel_code, auth=auth, change_type="收紧范围",
                before=before_dims, after=sorted(new_dims),
                operator=str(operator.get("姓名")), approver=str(approver.get("姓名")),
                result="通过",
                remark=f"授权收紧至{'、'.join(sorted(new_dims))}；图表{'、'.join(sorted(new_charts))}",
            )
            self._record_dependent_changes(panel, auth, dependents_before,
                                           str(operator.get("姓名")), str(approver.get("姓名")))
            self.recompute_chart_visibility(panel)
            return {"ok": True, "message": "授权范围已收紧并记录审批台账", "entry": auth}

        if action == "撤销授权":
            if str(auth.get("状态")) != AUTH_ACTIVE:
                return {"ok": False, "message": "只有已发布授权可以撤销"}
            if approver is None or str(approver.get("角色")) != ROLE_DEPT_HEAD:
                return {"ok": False, "message": "撤销授权必须记录部门负责人作为审批人"}
            dependents_before = self._dependent_scopes(panel_code, str(auth.get("授权编号")))
            auth["状态"] = "已撤销"
            auth["审批人"] = str(approver.get("姓名"))
            auth["操作人"] = str(operator.get("姓名"))
            self._append_approval(
                panel_code=panel_code, auth=auth, change_type="撤销授权",
                before=before_dims, after=[], operator=str(operator.get("姓名")),
                approver=str(approver.get("姓名")), result="通过",
                remark="撤销授权，继承来源下游同步失效，历史访问快照保留",
            )
            self._record_dependent_changes(panel, auth, dependents_before,
                                           str(operator.get("姓名")), str(approver.get("姓名")))
            self.recompute_chart_visibility(panel)
            return {"ok": True, "message": "授权已撤销，下游继承与图表可见状态同步失效", "entry": auth}

        return {"ok": False, "message": f"动作「{action}」不属于看板授权编排范围"}

    def _dependent_scopes(self, panel_code: str, parent_code: str) -> dict[str, dict[str, Any]]:
        before: dict[str, dict[str, Any]] = {}
        for auth in self._auth_rows(panel_code):
            if str(auth.get("授权编号")) == parent_code:
                continue
            if not self._inherits_from(auth, parent_code):
                continue
            scope = self._scope(auth)
            before[str(auth.get("授权编号"))] = {
                "auth": auth,
                "active": scope["active"],
                "dimensions": set(scope["dimensions"]),
                "charts": set(scope["charts"]),
            }
        return before

    def _record_dependent_changes(
        self,
        panel: dict[str, Any],
        parent_auth: dict[str, Any],
        before: dict[str, dict[str, Any]],
        operator: str,
        approver: str,
    ) -> None:
        panel_code = str(panel.get("面板编号"))
        for code, item in before.items():
            child = item["auth"]
            after_scope = self._scope(child)
            if item["active"] == after_scope["active"] and \
                    item["dimensions"] == after_scope["dimensions"] and \
                    item["charts"] == after_scope["charts"]:
                continue
            change_type = "继承失效" if not after_scope["active"] else "继承收紧"
            self._append_approval(
                panel_code=panel_code, auth=child, change_type=change_type,
                before=sorted(item["dimensions"]), after=sorted(after_scope["dimensions"]),
                operator=operator, approver=approver, result="通过",
                remark=(f"继承来源 {parent_auth.get('授权编号')} 变更，下游图表权限同步"
                        f"{'失效' if not after_scope['active'] else '收紧'}"),
            )

    # ------------------------------------------------------------------ 分享链接

    def create_share(self, entry_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        panel = self._panel(entry_id)
        if panel is None:
            return {"ok": False, "message": f"看板面板 {entry_id} 不存在或已归档"}
        if payload.get("分享令牌") or payload.get("share_token"):
            return {"ok": False, "message": "共享链接为只读，不能改动面板权限"}
        operator = self.resolve_member(payload.get("操作人") or payload.get("operator"))
        if operator is None:
            return {"ok": False, "message": "操作人不存在，无法创建分享链接"}
        role = str(operator.get("角色"))
        if role in (ROLE_MEMBER, ROLE_READONLY):
            return {"ok": False, "message": f"{role}角色不能共享面板"}
        auth = self._find_auth(str(panel.get("面板编号")),
                               payload.get("授权编号") or payload.get("authorization"))
        if auth is None:
            return {"ok": False, "message": "分享必须绑定一条已发布的看板授权"}
        scope = self._scope(auth)
        if not scope["active"]:
            return {"ok": False, "message": f"授权 {auth.get('授权编号')} 未生效或已撤销"}

        target_name = payload.get("目标团队") or payload.get("target_team") or auth.get("团队")
        target = self.resolve_team(target_name)
        if target is None:
            return {"ok": False, "message": "目标团队不存在"}
        target_team = str(target.get("团队名称"))
        own_team = str(operator.get("团队名称"))
        if role == ROLE_SUPERVISOR:
            if target_team != own_team or str(auth.get("团队")) != own_team:
                return {"ok": False, "message": "团队主管只能把面板共享给本部门，跨部门只读需部门负责人授权"}
            share_scope = "本部门只读"
        else:
            share_scope = "本部门只读" if target_team == own_team else "跨部门只读"

        dims = sorted(scope["dimensions"])
        charts = sorted(scope["charts"])
        shares_rows = store.rows(SHARE_TABLE)
        new_id = max((int(row.get("id", 0)) for row in shares_rows), default=0) + 1
        share = {
            "id": new_id,
            "分享编号": f"SL-{new_id:04d}",
            "分享令牌": f"SHARE-{target_team.replace('部', '').upper() or 'TEAM'}-{new_id:04d}",
            "面板编号": str(panel.get("面板编号")),
            "授权编号": str(auth.get("授权编号")),
            "目标团队": target_team,
            "可见范围": share_scope,
            "快照维度": dims,
            "快照图表": charts,
            "创建人": str(operator.get("姓名")),
            "创建人角色": role,
            "状态": "有效",
            "创建时间": _now(),
        }
        shares_rows.append(share)
        return {"ok": True, "message": "分享链接已创建，仅保留创建时只读快照", "entry": share,
                "read_only": True, "link": f"/share/{share['分享令牌']}"}

    def share_permission_change(self, entry_id: int, token: str) -> dict[str, Any]:
        panel = self._panel(entry_id)
        if panel is None:
            return {"ok": False, "message": f"看板面板 {entry_id} 不存在或已归档"}
        share = None
        for row in store.rows(SHARE_TABLE):
            if str(row.get("分享令牌")) == str(token):
                share = row
                break
        if share is None:
            return {"ok": False, "message": f"分享链接 {token} 不存在"}
        return {
            "ok": False,
            "status_code": 403,
            "message": "共享链接为只读，不能改动面板权限",
            "share": {
                "分享令牌": share.get("分享令牌"),
                "目标团队": share.get("目标团队"),
                "快照维度": share.get("快照维度"),
                "快照图表": share.get("快照图表"),
            },
        }

    # ------------------------------------------------------------------ 查询接口

    def permissions(self, entry_id: int) -> dict[str, Any] | None:
        panel = self._panel(entry_id)
        if panel is None:
            return None
        auth_items: list[dict[str, Any]] = []
        for auth in self._auth_rows(str(panel.get("面板编号"))):
            scope = self._scope(auth)
            auth_items.append({
                **auth,
                "生效": scope["active"],
                "生效维度": sorted(scope["dimensions"]),
                "生效图表": sorted(scope["charts"]),
                "继承链": scope["chain"],
                "失效原因": None if scope["active"] else scope["reason"],
            })
        panel_code = str(panel.get("面板编号"))
        shares = [row for row in store.rows(SHARE_TABLE)
                  if str(row.get("面板编号")) == panel_code]
        stats = {
            "授权总数": len(auth_items),
            "已生效": sum(1 for item in auth_items if item["生效"]),
            "待审批": sum(1 for item in auth_items if item.get("状态") == "待审批"),
            "已撤销": sum(1 for item in auth_items if item.get("状态") == "已撤销"),
            "分享链接": len(shares),
            "历史访问": sum(1 for row in store.rows(HISTORY_TABLE)
                            if str(row.get("面板编号")) == panel_code),
        }
        return {
            "panel": panel,
            "authorizations": auth_items,
            "shares": shares,
            "stats": stats,
            "conflict_rules": self.conflict_rules,
            "charts": self.recompute_chart_visibility(panel),
            "members": self._members(),
            "teams": self._teams(),
        }

    def chart_visibility(self, entry_id: int) -> dict[str, Any] | None:
        panel = self._panel(entry_id)
        if panel is None:
            return None
        return {"panel": {"id": panel.get("id"), "面板编号": panel.get("面板编号")},
                "items": self.recompute_chart_visibility(panel)}

    def shares(self, entry_id: int) -> list[dict[str, Any]] | None:
        panel = self._panel(entry_id)
        if panel is None:
            return None
        return [row for row in store.rows(SHARE_TABLE)
                if str(row.get("面板编号")) == str(panel.get("面板编号"))]

    def access_history(self, entry_id: int) -> dict[str, Any] | None:
        panel = self._panel(entry_id)
        if panel is None:
            return None
        rows = [row for row in store.rows(HISTORY_TABLE)
                if str(row.get("面板编号")) == str(panel.get("面板编号"))]
        rows = list(reversed(rows))
        return {"items": rows, "total": len(rows),
                "note": "历史访问保留访问当时授权快照，后续收紧或撤销不回写"}

    def approvals(self, entry_id: int) -> dict[str, Any] | None:
        panel = self._panel(entry_id)
        if panel is None:
            return None
        rows = [row for row in store.rows(APPROVAL_TABLE)
                if str(row.get("面板编号")) == str(panel.get("面板编号"))]
        rows = list(reversed(rows))
        return {"items": rows, "total": len(rows),
                "note": "授权发布、收紧、撤销与继承失效均记录团队、继承来源和审批人"}


dashboard_access_service = DashboardAccessService()
