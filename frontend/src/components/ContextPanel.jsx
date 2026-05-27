/**
 * 上下文管理面板（v1.13 · 蓝粉视觉）
 * - 四维指标：回捞次数 / stale 命中率 / 摘要遗漏数 / 压缩比
 * - 压缩策略对照子区（condenser A/B：策略版本化对比）
 */
import { useEffect, useState } from 'react'

const TOKEN = 'demo-token'

export default function ContextPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`/context`, { headers: { 'x-agent-token': TOKEN } })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="panel-error">上下文数据加载失败: {error}</div>
  if (!data) return <div className="panel-loading">加载上下文指标…</div>
  if (!data.enabled) return <div className="panel-empty">上下文指标未启用（后端未挂载）</div>

  const m = data.metrics
  return (
    <div className="panel" data-testid="context-panel">
      <h3 className="panel-title">🧠 上下文管理</h3>

      <div className="metric-grid">
        <div className="metric-card">
          <div className={`metric-value ${m.recall_rate > 0.3 ? 'danger' : ''}`}>{m.recall_count ?? 0}</div>
          <div className="metric-label">回捞次数（{((m.recall_rate ?? 0) * 100).toFixed(1)}%）</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${(m.stale_hit_rate ?? 1) > 0.05 ? 'danger' : ''}`}>{((m.stale_hit_rate ?? 0) * 100).toFixed(1)}%</div>
          <div className="metric-label">stale 命中率</div>
        </div>
        <div className="metric-card">
          <div className={`metric-value ${m.summary_misses > 0 ? 'danger' : ''}`}>{m.summary_misses ?? 0}</div>
          <div className="metric-label">摘要遗漏</div>
        </div>
        <div className="metric-card">
          <div className="metric-value accent">{((m.avg_compression_ratio ?? 0) * 100).toFixed(0)}%</div>
          <div className="metric-label">平均压缩比（{m.compression_events ?? 0} 次）</div>
        </div>
      </div>

      {/* 压缩策略对照子区（condenser A/B · v1.13） */}
      <div className="section">
        <h4 className="section-title">压缩策略对照（A/B）</h4>
        <p className="section-hint">同一事件流、不同 condenser 管道配置对比——把"λ 设 0.1 还是 0.3"变成评测数据。</p>
        <div className="policy-list">
          {(data.policies || []).map((p) => (
            <div key={p} className="policy-chip">
              <span className="policy-name">{p}</span>
              <span className="policy-tag">{p === 'default' ? '基准' : p.includes('aggressive') ? '激进遗忘' : '温和'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
