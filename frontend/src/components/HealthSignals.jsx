/**
 * 健康信号面板（v1.13 · 蓝粉视觉）
 * - 卡检测 stuck（重复工具调用/相同错误反复出现）
 * - 会话健康度（四维指标 + stuck 频率 → 建议交接/新会话）
 */
import { useEffect, useState } from 'react'

const TOKEN = 'demo-token'

export default function HealthSignals() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`/health-signals`, { headers: { 'x-agent-token': TOKEN } })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="panel-error">健康信号加载失败: {error}</div>
  if (!data) return <div className="panel-loading">加载健康信号…</div>
  if (!data.enabled) return <div className="panel-empty">健康信号未启用（后端未挂载）</div>

  const r = data.report
  const levelClass = r.level === 'healthy' ? 'ok' : r.level === 'degraded' ? 'warn' : 'danger'
  return (
    <div className="panel" data-testid="health-signals">
      <h3 className="panel-title">💓 健康信号</h3>

      <div className={`health-level ${levelClass}`}>
        <span className="health-dot" />
        {r.level === 'healthy' ? '会话健康' : r.level === 'degraded' ? '会话退化' : '会话告急'}
        <span className="health-score">{Math.round(r.score)}/100</span>
      </div>

      <div className="metric-grid">
        <div className="metric-card">
          <div className={`metric-value ${r.stuck_count > 0 ? 'warn' : ''}`}>{r.stuck_count}</div>
          <div className="metric-label">stuck 信号</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${r.refetch_rate > 0.3 ? 'danger' : ''}`}>{((r.refetch_rate ?? 0) * 100).toFixed(1)}%</div>
          <div className="metric-label">回捞率</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${r.summary_miss > 0 ? 'warn' : ''}`}>{r.summary_miss}</div>
          <div className="metric-label">摘要遗漏</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${(r.stale_hit_rate ?? 1) > 0.05 ? 'danger' : ''}`}>{((r.stale_hit_rate ?? 0) * 100).toFixed(1)}%</div>
          <div className="metric-label">stale 命中</div>
        </div>
      </div>

      <div className="health-advice">{r.advice}</div>
    </div>
  )
}
