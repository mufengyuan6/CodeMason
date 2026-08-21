/**
 * 成本驾驶舱（v1.13 · 蓝粉视觉）
 * - 每 Op token 消耗/节省台账
 * - 高成本操作预警列表
 */
import { useEffect, useState } from 'react'

const TOKEN = 'demo-token'

export default function CostCockpit() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`/costs`, { headers: { 'x-agent-token': TOKEN } })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="panel-error">成本数据加载失败: {error}</div>
  if (!data) return <div className="panel-loading">加载成本台账…</div>
  if (!data.enabled) return <div className="panel-empty">成本台账未启用（后端未挂载）</div>

  const s = data.summary
  return (
    <div className="panel" data-testid="cost-cockpit">
      <h3 className="panel-title">⚡ 成本驾驶舱</h3>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-value accent">{s.total_tokens_saved?.toLocaleString() ?? 0}</div>
          <div className="metric-label">累计节省 tokens</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{s.total_ops ?? 0}</div>
          <div className="metric-label">总操作数</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{((s.save_ratio ?? 0) * 100).toFixed(1)}%</div>
          <div className="metric-label">节省率</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${(s.high_cost_count ?? 0) > 0 ? 'danger' : ''}`}>{s.high_cost_count ?? 0}</div>
          <div className="metric-label">高成本预警</div>
        </div>
      </div>

      {data.high_cost?.length > 0 && (
        <div className="section">
          <h4 className="section-title">⚠️ 高成本操作预警</h4>
          {data.high_cost.map((r) => (
            <div key={r.op_id} className="cost-row warn">
              <span className="cost-op">{r.op_type}</span>
              <span className="cost-tokens">{(r.tokens_in + r.tokens_out).toLocaleString()} tokens</span>
              <span className="cost-msg">{r.warn}</span>
            </div>
          ))}
        </div>
      )}

      {data.by_op_type && (
        <div className="section">
          <h4 className="section-title">按操作类型</h4>
          <table className="cost-table">
            <thead>
              <tr>
                <th>Op 类型</th>
                <th>次数</th>
                <th>Tokens</th>
                <th>节省</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.by_op_type).map(([type, agg]) => (
                <tr key={type}>
                  <td>{type}</td>
                  <td>{agg.count}</td>
                  <td>{agg.tokens.toLocaleString()}</td>
                  <td className="accent">+{agg.saved.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
