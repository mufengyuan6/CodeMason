/**
 * AI 贡献审计视图（v1.23 · G17⑧ 落地）
 * - ContributionReport 纯事件投影导出（零 LLM）
 * - 文件贡献 + AI 参与度 + 验证证据 + 成本
 * - 变更归属标注（"AI 写的就要标出来"，EU AI Act Article 50）
 */
import { useEffect, useState } from 'react'

const TOKEN = 'demo-token'

export default function ContributionPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [taskId, setTaskId] = useState('task-1')

  const load = (tid) => {
    setError(null)
    fetch(`/api/contribution?task_id=${encodeURIComponent(tid)}`, { headers: { 'x-agent-token': TOKEN } })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)))
  }

  useEffect(() => {
    load(taskId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (error) return <div className="panel-error">贡献报告加载失败: {error}</div>
  if (!data) return <div className="panel-loading">加载贡献报告…</div>
  if (!data.enabled) return <div className="panel-empty">贡献报告未启用（后端未挂载）</div>

  const r = data.report
  const involvement = r.ai_involvement
  const involvementLabel = { full_auto: '全自动', assisted: '人机协作', human_led: '人类主导' }[involvement] || involvement

  return (
    <div className="panel" data-testid="contribution-panel">
      <h3 className="panel-title">📜 AI 贡献审计</h3>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-value accent">{involvementLabel}</div>
          <div className="metric-label">AI 参与度</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{r.files?.length ?? 0}</div>
          <div className="metric-label">AI 修改文件</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{r.cost?.tokens ?? 0}</div>
          <div className="metric-label">Tokens</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{(r.cost?.duration_s ?? 0).toFixed(1)}s</div>
          <div className="metric-label">耗时</div>
        </div>
      </div>

      <div className="section">
        <h4 className="section-title">文件贡献（可下钻 provenance）</h4>
        {r.files?.length === 0 && <div className="panel-empty">暂无文件贡献</div>}
        {r.files?.map((f) => (
          <div key={f.path} className="cost-row">
            <span className="cost-op">{f.path}</span>
            {f.line_range?.length === 2 && <span className="cost-tokens">L{f.line_range[0]}-{f.line_range[1]}</span>}
            <span className={`contribution-badge ${f.changed_by === 'ai' ? 'badge-ai' : 'badge-human'}`}>{f.changed_by === 'ai' ? 'AI' : '协作'}</span>
            <span className="cost-msg" title={f.provenance_event_ids?.join(', ')}>事件 {f.provenance_event_ids?.length ?? 0} 条</span>
          </div>
        ))}
      </div>

      <div className="section">
        <h4 className="section-title">验证证据</h4>
        <div className="cost-row">
          <span className="cost-op">测试</span>
          <span className="cost-tokens">{r.verification?.tests_run ?? 0} 次</span>
          <span className="cost-msg">门禁: {r.verification?.gate_status ?? 'unknown'}</span>
        </div>
      </div>

      <div className="section">
        <h4 className="section-title">变更归属标注（git commit metadata）</h4>
        <pre className="attribution-block">{`Co-Authored-By: CodeMason AI Agent
X-CodeMason-Involvement: ${involvement}
X-CodeMason-Verification: ${r.verification?.gate_status ?? 'unknown'}`}</pre>
      </div>
    </div>
  )
}
