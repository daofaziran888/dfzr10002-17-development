import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchJson, request } from '@/api/client'

type Member = { 姓名: string; 成员编号: string; 团队名称: string; 角色: string }
type Team = { 团队名称: string; 团队编号: string }

type Authorization = {
  id: number
  授权编号: string
  团队: string
  可见范围: string
  允许维度: string[]
  允许图表: string[]
  继承来源: string | null
  状态: string
  审批状态: string
  操作人: string
  审批人: string | null
  版本: number
  生效: boolean
  生效维度: string[]
  生效图表: string[]
  继承链: string[]
  失效原因: string | null
}

type ChartVisibility = {
  图表编号: string
  图表名称: string
  可见状态: string
  可见团队: string
  可见维度: string
  授权编号: string[]
}

type Share = {
  分享编号: string
  分享令牌: string
  授权编号: string
  目标团队: string
  可见范围: string
  快照维度: string[]
  快照图表: string[]
  创建人: string
  创建人角色: string
  状态: string
  创建时间: string
}

type Approval = {
  台账编号: string
  变更类型: string
  授权编号: string
  团队: string
  继承来源: string | null
  变更前维度: string[]
  变更后维度: string[]
  操作人: string
  审批人: string | null
  审批结果: string
  变更时间: string
  备注: string
}

type HistoryItem = {
  访问编号: string
  访问人: string
  团队: string
  角色: string
  访问类型: string
  访问时间: string
  请求维度: string[]
  授权快照: Record<string, unknown>
  结果: string
  拒绝原因: string | null
  分享链接: string | null
}

type Permissions = {
  panel: Record<string, string | number>
  authorizations: Authorization[]
  shares: Share[]
  stats: Record<string, number>
  conflict_rules: string[]
  charts: ChartVisibility[]
  members: Member[]
  teams: Team[]
}

type Evaluation = {
  allowed: boolean
  reason: string
  read_only: boolean
  visible_scope: string
  dimensions: string[]
  charts_to_refresh: string[]
  excluded_charts: string[]
  denied_dimensions: string[]
  inheritance_chain: string[]
  authorizations: string[]
  conflict_resolutions: { 规则: string; 来源: string[]; 结果维度?: string[]; 结果图表?: string[]; 说明?: string }[]
  history_record: { 访问编号: string; 结果: string } | null
}

type ActionResult = {
  ok: boolean
  message: string
  entry?: Record<string, unknown> | null
}

const DIMENSION_OPTIONS = ['销售额', '地区', '渠道', '成本']
const CHART_OPTIONS = ['CHAR-0004A', 'CHAR-0004B']
const VISIBLE_SCOPES = ['本部门', '本部门只读', '跨部门只读']

function splitValues(text: string): string[] {
  return text
    .split(/[、,，;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((item) => item !== value) : [...list, value]
}

export default function PermissionPanel({ panelId }: { panelId: number }) {
  const [data, setData] = useState<Permissions | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')

  const [operator, setOperator] = useState('孙磊')
  const [approver, setApprover] = useState('何静')

  const [newTeam, setNewTeam] = useState('财务部')
  const [newScope, setNewScope] = useState('本部门')
  const [newDimensions, setNewDimensions] = useState<string[]>(['销售额', '地区'])
  const [newCharts, setNewCharts] = useState<string[]>(['CHAR-0004A'])
  const [newParent, setNewParent] = useState('AUTH-0001')

  const [editingCode, setEditingCode] = useState<string | null>(null)
  const [editDimensions, setEditDimensions] = useState<string[]>([])
  const [editCharts, setEditCharts] = useState<string[]>([])

  const [checkMember, setCheckMember] = useState('张会计')
  const [checkDimensions, setCheckDimensions] = useState('销售额、地区')
  const [checkShare, setCheckShare] = useState('')
  const [checkAction, setCheckAction] = useState('view')
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)

  const [shareAuth, setShareAuth] = useState('AUTH-0002')
  const [shareTeam, setShareTeam] = useState('财务部')
  const [shareOperator, setShareOperator] = useState('李敏')

  const members = data?.members ?? []
  const teams = data?.teams ?? []
  const publishableAuths = useMemo(
    () => (data?.authorizations ?? []).filter((auth) => auth.生效),
    [data],
  )

  const reload = useCallback(async () => {
    setErrorMessage('')
    try {
      const payload = await fetchJson<Permissions>(`/api/dashboard/${panelId}/permissions`)
      setData(payload)
      const [historyPayload, approvalPayload] = await Promise.all([
        fetchJson<{ items: HistoryItem[] }>(`/api/dashboard/${panelId}/access-history`),
        fetchJson<{ items: Approval[] }>(`/api/dashboard/${panelId}/approvals`),
      ])
      setHistory(historyPayload.items ?? [])
      setApprovals(approvalPayload.items ?? [])
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '看板权限数据读取失败')
    }
  }, [panelId])

  useEffect(() => {
    void reload()
  }, [reload])

  async function submitAction(path: string, body: Record<string, unknown>, successMessage: string) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
      const payload = (await response.json().catch(() => ({}))) as ActionResult & { detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail ?? payload.message ?? `接口返回 ${response.status}`)
      }
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '操作未生效')
        return
      }
      setNotice(payload.message || successMessage)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '看板权限操作失败')
    }
  }

  async function evaluateAccess() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`/api/dashboard/${panelId}/access/evaluate`, {
        method: 'POST',
        body: JSON.stringify({
          成员: checkMember,
          请求维度: splitValues(checkDimensions),
          分享令牌: checkShare || null,
          动作: checkAction,
        }),
      })
      const payload = (await response.json()) as Evaluation & { detail?: string }
      if (!response.ok) {
        throw new Error(payload.detail ?? `接口返回 ${response.status}`)
      }
      setEvaluation(payload)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '访问判定失败')
    }
  }

  function startTighten(auth: Authorization) {
    setEditingCode(auth.授权编号)
    setEditDimensions(auth.允许维度)
    setEditCharts(auth.允许图表)
  }

  async function submitTighten() {
    if (!editingCode) return
    await submitAction(
      `/api/dashboard/${panelId}/permissions/${editingCode}/actions`,
      {
        action: '收紧范围',
        操作人: operator,
        审批人: approver,
        允许维度: editDimensions,
        允许图表: editCharts,
      },
      '授权范围已收紧',
    )
    setEditingCode(null)
  }

  async function revokeAuthorization(auth: Authorization) {
    await submitAction(
      `/api/dashboard/${panelId}/permissions/${auth.授权编号}/actions`,
      { action: '撤销授权', 操作人: operator, 审批人: approver },
      '授权已撤销',
    )
  }

  async function approveAuthorization(auth: Authorization, action: '审批通过' | '驳回') {
    await submitAction(
      `/api/dashboard/${panelId}/permissions/${auth.授权编号}/actions`,
      { action, 操作人: operator, 审批人: approver },
      '审批动作已记录',
    )
  }

  async function publishAuthorization() {
    await submitAction(
      `/api/dashboard/${panelId}/permissions`,
      {
        操作人: operator,
        审批人: approver,
        团队: newTeam,
        可见范围: newScope,
        允许维度: newDimensions,
        允许图表: newCharts,
        继承来源: newParent || null,
      },
      '授权已发布',
    )
  }

  async function createShare() {
    await submitAction(
      `/api/dashboard/${panelId}/shares`,
      { 操作人: shareOperator, 授权编号: shareAuth, 目标团队: shareTeam },
      '分享链接已创建',
    )
  }

  async function tryChangeShare(share: Share) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`/api/dashboard/${panelId}/shares/${share.分享令牌}`, {
        method: 'POST',
        body: JSON.stringify({ 操作人: shareOperator, 动作: '调整授权' }),
      })
      const payload = (await response.json().catch(() => ({}))) as { detail?: string; message?: string }
      if (!response.ok) {
        throw new Error(payload.detail ?? payload.message ?? `接口返回 ${response.status}`)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '共享链接改动被拒绝')
    }
  }

  if (!data) {
    return (
      <section className="perm-panel">
        <p className="page-desc">{errorMessage || '正在读取看板授权编排…'}</p>
      </section>
    )
  }

  return (
    <section className="perm-panel">
      <div className="perm-head">
        <div>
          <h3>看板权限编排 · {String(data.panel.面板编号)} {String(data.panel.面板名称)}</h3>
          <p className="page-desc">发布时绑定团队、可见范围、允许联动维度与继承来源；冲突按最严优先裁决。</p>
        </div>
        <div className="perm-operator">
          <label className="filter-item">
            <span>操作人</span>
            <select value={operator} onChange={(event) => setOperator(event.target.value)}>
              {members.map((member) => (
                <option key={member.成员编号} value={member.姓名}>
                  {member.姓名}（{member.团队名称}/{member.角色}）
                </option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>审批人</span>
            <select value={approver} onChange={(event) => setApprover(event.target.value)}>
              {members.map((member) => (
                <option key={member.成员编号} value={member.姓名}>
                  {member.姓名}（{member.团队名称}/{member.角色}）
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {(errorMessage || notice) && (
        <p className={errorMessage ? 'error-text' : 'notice-text'}>{errorMessage || notice}</p>
      )}

      <div className="stat-row">
        {Object.entries(data.stats).map(([label, value]) => (
          <article key={label} className="stat-card">
            <span className="stat-label">{label}</span>
            <strong className="stat-value">{value}</strong>
          </article>
        ))}
      </div>

      <details className="perm-rules">
        <summary>冲突裁决规则</summary>
        <ol>
          {data.conflict_rules.map((rule) => <li key={rule}>{rule}</li>)}
        </ol>
      </details>

      <h4>图表配置可见状态</h4>
      <table className="data-table">
        <thead>
          <tr><th>图表编号</th><th>图表名称</th><th>可见状态</th><th>可见团队</th><th>可见维度</th><th>授权编号</th></tr>
        </thead>
        <tbody>
          {data.charts.map((chart) => (
            <tr key={chart.图表编号}>
              <td>{chart.图表编号}</td>
              <td>{chart.图表名称}</td>
              <td>{chart.可见状态}</td>
              <td>{chart.可见团队}</td>
              <td>{chart.可见维度}</td>
              <td>{chart.授权编号.join('、') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>团队授权</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>授权编号</th><th>团队</th><th>可见范围</th><th>允许维度</th><th>允许图表</th>
            <th>继承来源</th><th>状态</th><th>审批人</th><th>生效范围</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.authorizations.map((auth) => (
            <tr key={auth.授权编号}>
              <td>{auth.授权编号}</td>
              <td>{auth.团队}</td>
              <td>{auth.可见范围}</td>
              <td>{auth.允许维度.join('、')}</td>
              <td>{auth.允许图表.join('、')}</td>
              <td>{auth.继承来源 ?? '—'}</td>
              <td>{auth.状态}</td>
              <td>{auth.审批人 ?? '—'}</td>
              <td>{auth.生效 ? `维度 ${auth.生效维度.join('、')}；图表 ${auth.生效图表.join('、')}` : auth.失效原因}</td>
              <td className="row-actions">
                {auth.状态 === '待审批' && (
                  <>
                    <button className="link" type="button" onClick={() => void approveAuthorization(auth, '审批通过')}>审批通过</button>
                    <button className="link" type="button" onClick={() => void approveAuthorization(auth, '驳回')}>驳回</button>
                  </>
                )}
                {auth.状态 === '已发布' && (
                  <>
                    <button className="link" type="button" onClick={() => startTighten(auth)}>收紧范围</button>
                    <button className="link" type="button" onClick={() => void revokeAuthorization(auth)}>撤销授权</button>
                  </>
                )}
              </td>
            </tr>
          ))}
          {data.authorizations.length === 0 && (
            <tr><td className="empty-state" colSpan={10}>暂无团队授权，先在下方发布授权</td></tr>
          )}
        </tbody>
      </table>

      {editingCode && (
        <div className="perm-form">
          <h4>收紧授权 {editingCode}</h4>
          <div className="perm-checks">
            {DIMENSION_OPTIONS.map((dimension) => (
              <label key={dimension}>
                <input
                  type="checkbox"
                  checked={editDimensions.includes(dimension)}
                  onChange={() => setEditDimensions((prev) => toggle(prev, dimension))}
                />
                {dimension}
              </label>
            ))}
            {CHART_OPTIONS.map((chart) => (
              <label key={chart}>
                <input
                  type="checkbox"
                  checked={editCharts.includes(chart)}
                  onChange={() => setEditCharts((prev) => toggle(prev, chart))}
                />
                {chart}
              </label>
            ))}
          </div>
          <div className="perm-form-actions">
            <button className="btn primary" type="button" onClick={() => void submitTighten()}>提交收紧</button>
            <button className="btn ghost" type="button" onClick={() => setEditingCode(null)}>取消</button>
          </div>
        </div>
      )}

      <h4>发布团队授权</h4>
      <div className="perm-form">
        <div className="perm-form-grid">
          <label className="filter-item">
            <span>团队</span>
            <select value={newTeam} onChange={(event) => setNewTeam(event.target.value)}>
              {teams.map((team) => <option key={team.团队编号} value={team.团队名称}>{team.团队名称}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>可见范围</span>
            <select value={newScope} onChange={(event) => setNewScope(event.target.value)}>
              {VISIBLE_SCOPES.map((scope) => <option key={scope} value={scope}>{scope}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>继承来源</span>
            <select value={newParent} onChange={(event) => setNewParent(event.target.value)}>
              <option value="">不继承</option>
              {publishableAuths.map((auth) => (
                <option key={auth.授权编号} value={auth.授权编号}>{auth.授权编号}（{auth.团队}）</option>
              ))}
            </select>
          </label>
        </div>
        <div className="perm-checks">
          <span>允许维度：</span>
          {DIMENSION_OPTIONS.map((dimension) => (
            <label key={dimension}>
              <input
                type="checkbox"
                checked={newDimensions.includes(dimension)}
                onChange={() => setNewDimensions((prev) => toggle(prev, dimension))}
              />
              {dimension}
            </label>
          ))}
        </div>
        <div className="perm-checks">
          <span>允许图表：</span>
          {CHART_OPTIONS.map((chart) => (
            <label key={chart}>
              <input
                type="checkbox"
                checked={newCharts.includes(chart)}
                onChange={() => setNewCharts((prev) => toggle(prev, chart))}
              />
              {chart}
            </label>
          ))}
        </div>
        <div className="perm-form-actions">
          <button className="btn primary" type="button" onClick={() => void publishAuthorization()}>发布授权</button>
          <span className="page-desc">团队主管仅限本部门；部门负责人可跨部门只读；继承来源不能扩权。</span>
        </div>
      </div>

      <h4>访问判定（越权筛选演练）</h4>
      <div className="perm-form">
        <div className="perm-form-grid">
          <label className="filter-item">
            <span>成员</span>
            <select value={checkMember} onChange={(event) => setCheckMember(event.target.value)}>
              {members.map((member) => (
                <option key={member.成员编号} value={member.姓名}>
                  {member.姓名}（{member.团队名称}/{member.角色}）
                </option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>请求维度（顿号分隔）</span>
            <input value={checkDimensions} onChange={(event) => setCheckDimensions(event.target.value)} />
          </label>
          <label className="filter-item">
            <span>动作</span>
            <select value={checkAction} onChange={(event) => setCheckAction(event.target.value)}>
              <option value="view">查看</option>
              <option value="调整授权">调整授权</option>
            </select>
          </label>
          <label className="filter-item">
            <span>分享令牌（可选）</span>
            <select value={checkShare} onChange={(event) => setCheckShare(event.target.value)}>
              <option value="">不使用分享链接</option>
              {data.shares.map((share) => (
                <option key={share.分享令牌} value={share.分享令牌}>{share.分享令牌}（{share.目标团队}）</option>
              ))}
            </select>
          </label>
        </div>
        <div className="perm-form-actions">
          <button className="btn primary" type="button" onClick={() => void evaluateAccess()}>判定权限</button>
        </div>
      </div>

      {evaluation && (
        <div className={evaluation.allowed ? 'perm-result' : 'perm-result rejected'}>
          <p><strong>{evaluation.allowed ? '允许' : '拒绝'}</strong>：{evaluation.reason}</p>
          <p>可见维度：{evaluation.dimensions.join('、') || '—'}；参与刷新图表：{evaluation.charts_to_refresh.join('、') || '—'}</p>
          {evaluation.excluded_charts.length > 0 && <p>未授权图表：{evaluation.excluded_charts.join('、')}</p>}
          {evaluation.denied_dimensions.length > 0 && <p>越权维度：{evaluation.denied_dimensions.join('、')}</p>}
          <p>继承链：{evaluation.inheritance_chain.join(' → ') || '—'}；读写：{evaluation.read_only ? '只读' : '可查看'}</p>
          {evaluation.conflict_resolutions.map((resolution, index) => (
            <p key={`${resolution.规则}-${index}`} className="page-desc">
              裁决：{resolution.规则}；结果维度 {resolution.结果维度?.join('、') ?? '—'}
            </p>
          ))}
          {evaluation.history_record && <p className="page-desc">已写入访问历史：{evaluation.history_record.访问编号}（{evaluation.history_record.结果}）</p>}
        </div>
      )}

      <h4>分享链接（只读快照）</h4>
      <table className="data-table">
        <thead>
          <tr><th>分享令牌</th><th>目标团队</th><th>可见范围</th><th>快照维度</th><th>快照图表</th><th>创建人</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          {data.shares.map((share) => (
            <tr key={share.分享令牌}>
              <td>{share.分享令牌}</td>
              <td>{share.目标团队}</td>
              <td>{share.可见范围}</td>
              <td>{share.快照维度.join('、')}</td>
              <td>{share.快照图表.join('、')}</td>
              <td>{share.创建人}（{share.创建人角色}）</td>
              <td>{share.状态}</td>
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void tryChangeShare(share)}>尝试改动权限</button>
              </td>
            </tr>
          ))}
          {data.shares.length === 0 && (
            <tr><td className="empty-state" colSpan={8}>暂无分享链接</td></tr>
          )}
        </tbody>
      </table>

      <div className="perm-form">
        <div className="perm-form-grid">
          <label className="filter-item">
            <span>绑定授权</span>
            <select value={shareAuth} onChange={(event) => setShareAuth(event.target.value)}>
              {data.authorizations.map((auth) => (
                <option key={auth.授权编号} value={auth.授权编号}>
                  {auth.授权编号}（{auth.团队}/{auth.状态}）
                </option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>目标团队</span>
            <select value={shareTeam} onChange={(event) => setShareTeam(event.target.value)}>
              {teams.map((team) => <option key={team.团队编号} value={team.团队名称}>{team.团队名称}</option>)}
            </select>
          </label>
          <label className="filter-item">
            <span>操作人</span>
            <select value={shareOperator} onChange={(event) => setShareOperator(event.target.value)}>
              {members.map((member) => (
                <option key={member.成员编号} value={member.姓名}>
                  {member.姓名}（{member.团队名称}/{member.角色}）
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="perm-form-actions">
          <button className="btn" type="button" onClick={() => void createShare()}>创建只读分享链接</button>
          <span className="page-desc">分享链接保持原只读快照，不能改动面板权限。</span>
        </div>
      </div>

      <h4>审批台账（范围变化可追溯）</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>台账编号</th><th>变更类型</th><th>授权编号</th><th>团队</th><th>继承来源</th>
            <th>变更前维度</th><th>变更后维度</th><th>操作人</th><th>审批人</th><th>变更时间</th><th>备注</th>
          </tr>
        </thead>
        <tbody>
          {approvals.map((approval) => (
            <tr key={approval.台账编号}>
              <td>{approval.台账编号}</td>
              <td>{approval.变更类型}</td>
              <td>{approval.授权编号}</td>
              <td>{approval.团队}</td>
              <td>{approval.继承来源 ?? '—'}</td>
              <td>{approval.变更前维度.join('、') || '—'}</td>
              <td>{approval.变更后维度.join('、') || '—'}</td>
              <td>{approval.操作人}</td>
              <td>{approval.审批人 ?? '—'}</td>
              <td>{approval.变更时间}</td>
              <td>{approval.备注}</td>
            </tr>
          ))}
          {approvals.length === 0 && (
            <tr><td className="empty-state" colSpan={11}>暂无审批台账</td></tr>
          )}
        </tbody>
      </table>

      <h4>历史访问（保留原授权快照）</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>访问编号</th><th>访问人</th><th>团队/角色</th><th>请求维度</th>
            <th>授权快照</th><th>结果</th><th>拒绝原因</th><th>分享链接</th><th>访问时间</th>
          </tr>
        </thead>
        <tbody>
          {history.map((item) => (
            <tr key={item.访问编号}>
              <td>{item.访问编号}</td>
              <td>{item.访问人}</td>
              <td>{item.团队}/{item.角色}</td>
              <td>{item.请求维度.join('、')}</td>
              <td>{JSON.stringify(item.授权快照)}</td>
              <td>{item.结果}</td>
              <td>{item.拒绝原因 ?? '—'}</td>
              <td>{item.分享链接 ?? '—'}</td>
              <td>{item.访问时间}</td>
            </tr>
          ))}
          {history.length === 0 && (
            <tr><td className="empty-state" colSpan={9}>暂无访问历史</td></tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
