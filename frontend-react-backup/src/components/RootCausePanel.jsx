/**
 * 溯源报告面板（v1.28 · G20 事件驱动根因分析可视化）
 * - 从事件流中提取 RootCauseReport 事件（溯源即事件——事件流投影，零新存储）
 * - 展示：触发源 / 三阶段定位（search/read/edit）/ 归因假设（agent_inferred 标注）
 *   / 修复指令 / 证据链摘要
 * - 与 G20 溯源报告视图同构：失败/疑问才溯源，非全库扫描（结构性防误报）
 * - 严格抽屉化不升 tab（v1.14 约束：主流程不被监控稀释）
 */
import { useMemo } from 'react'

function extractRootCauseReports(events) {
  if (!Array.isArray(events)) return []
  return events
    .filter((ev) => ev.type === 'RootCauseReport')
    .map((ev) => ({
      id: ev.id,
      report_id: ev.report_id,
      trigger: ev.trigger,
      status: ev.status,
      trigger_event_id: ev.trigger_event_id,
      stages: ev.stages || [],
      attributions: ev.attributions || [],
      fix_instructions: ev.fix_instructions || [],
      evidence: ev.evidence || {},
      ts: ev.ts,
    }))
    .reverse() // 最新在前
}

const TRIGGER_LABEL = {
  verify_failed: '验证失败',
  error: '错误事件',
  user_query: '用户疑问',
}

export default function RootCausePanel({ events = [] }) {
  const reports = useMemo(() => extractRootCauseReports(events), [events])

  return (
    <div className="panel" data-testid="root-cause-panel">
      <h3 className="panel-title">🔍 溯源报告</h3>
      {reports.length === 0 && (
        <div className="panel-empty">
          暂无溯源报告。失败/疑问会触发根因分析（G20）——确定性证据链 + 归因假设，溯源报告落事件流可审计。
        </div>
      )}
      {reports.map((r) => (
        <div key={r.report_id} className="rc-entry" data-testid={`rc-entry-${r.report_id}`}>
          <div className="rc-head">
            <span className="rc-trigger">{TRIGGER_LABEL[r.trigger] || r.trigger}</span>
            <span className={`rc-status ${r.status === 'completed' ? 'rc-ok' : 'rc-degraded'}`}>
              {r.status === 'completed' ? '完整链路' : '纯确定性'}
            </span>
            <span className="rc-id">{r.report_id}</span>
          </div>

          {r.stages.length > 0 && (
            <div className="rc-section">
              <b>阶段定位（TRAJEVAL 口径）</b>
              <ul className="rc-list">
                {r.stages.map((s, i) => (
                  <li key={i} className="rc-stage" data-testid="rc-stage">
                    <span className={`rc-stage-badge rc-stage-${s.stage}`}>{s.stage}</span>
                    <span className="rc-stage-issue">{s.issue}</span>
                    {typeof s.confidence === 'number' && (
                      <span className="rc-stage-conf">{(s.confidence * 100).toFixed(0)}%</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {r.attributions.length > 0 && (
            <div className="rc-section">
              <b>归因假设</b>
              <ul className="rc-list">
                {r.attributions.map((a, i) => (
                  <li key={i} className="rc-attribution" data-testid="rc-attribution">
                    <span>{a.hypothesis}</span>
                    {a.agent_inferred && <span className="metric-chip">agent_inferred</span>}
                    {typeof a.confidence === 'number' && (
                      <span className="rc-conf">{(a.confidence * 100).toFixed(0)}%</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {r.fix_instructions.length > 0 && (
            <div className="rc-section">
              <b>修复指令（机读可消费）</b>
              <ul className="rc-list">
                {r.fix_instructions.map((ins, i) => (
                  <li key={i} className="rc-fix" data-testid="rc-fix">{ins}</li>
                ))}
              </ul>
            </div>
          )}

          {Object.keys(r.evidence).length > 0 && (
            <details className="rc-evidence">
              <summary>证据链摘要（{Object.keys(r.evidence).filter((k) => r.evidence[k]?.length).length} 类）</summary>
              <pre className="rc-evidence-pre">
                {JSON.stringify(r.evidence, null, 2).slice(0, 800)}
              </pre>
            </details>
          )}
        </div>
      ))}
    </div>
  )
}
