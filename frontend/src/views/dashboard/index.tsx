import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'
import PermissionPanel from './PermissionPanel'

const ENDPOINT = '/api/dashboard'
const columns = [
  "面板编号",
  "面板名称",
  "布局配置",
  "图表集合",
  "刷新频率",
  "可见范围",
  "创建人",
  "是否展示"
]
const actions = [
  "提交校验",
  "启用配置",
  "停用配置"
]
const statuses = [
  "草稿",
  "待校验",
  "已启用",
  "已停用"
]
const stats = [
  {
    "label": "看板面板总数",
    "value": 0
  },
  {
    "label": "待校验面板",
    "value": 0
  },
  {
    "label": "已启用面板",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

export default function DashboardView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const [selectedPanel, setSelectedPanel] = useState<number | null>(null)

  useEffect(() => {
    const panel = rows.find((row) => String(row.面板编号) === 'DASH-0004')
    if (panel && selectedPanel === null) {
      setSelectedPanel(Number(panel.id))
    }
  }, [rows, selectedPanel])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('看板面板列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据看板列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('数据看板动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据看板操作失败')
    }
  }

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>数据看板管理</h2>
          <p className="page-desc">维护数据看板的面板布局、图表组合与可见范围，实现分析结果集中展示。</p>
          <p className="page-desc">按团队归属控制可见范围，跨部门越权筛选会被拒绝；授权调整与审批台账记录在下方权限编排里。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('看板面板登记入口尚未接入审批流')}>
            登记看板面板
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据看板清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)} className={selectedPanel === Number(row.id) ? 'row-selected' : undefined}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => setSelectedPanel(Number(row.id))}>权限编排</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无数据看板数据，可先登记看板面板</td>
            </tr>
          )}
        </tbody>
      </table>

      {selectedPanel !== null && <PermissionPanel panelId={selectedPanel} />}

      <footer className="page-foot">
        <span>共 {total} 条数据看板记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
