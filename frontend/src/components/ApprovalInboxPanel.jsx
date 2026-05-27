/**
 * 审批收件箱视图（v1.23 · G14 落地）
 * - 只收分类器拦截/存疑件（人类审拦截件，不审每个动作——v1.21 语义升级）
 * - approve / reject / edit 处置（幂等）
 */
import { useCallback, useEffect, useState } from 'react'

const TOKEN = 'demo-token'

export default function ApprovalInboxPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)
  const [editCmd, setEditCmd] = useState('')

  const load = useCallback(() => {
    fetch('/api/inbox', { headers: { 'x-agent-token': TOKEN } })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const respond = async (itemId, decision, editedCommand) => {
    const res = await fetch('/api/inbox/respond', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-agent-token': TOKEN },
      body: JSON.stringify({ item_id: itemId, decision, edited_command: editedCommand }),
    })
    if (!res.ok) throw new Error('处置失败')
    load()
  }

  if (error) return <div className="panel-error">收件箱加载失败: {error}</div>
  if (!data) return <div className="panel-loading">加载审批收件箱…</div>
  if (!data.enabled) return <div className="panel-empty">审批收件箱未启用（后端未挂载）</div>

  const s = data.stats || {}

  return (
    <div className="panel" data-testid="approval-inbox-panel">
      <h3 className="panel-title">📥 审批收件箱 <span className="title-sub">只审分类器拦截件</span></h3>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-value accent">{s.pending ?? 0}</div>
          <div className="metric-label">待处置</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{s.by_decision?.block ?? 0}</div>
          <div className="metric-label">拦截件</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{s.by_decision?.escalate ?? 0}</div>
          <div className="metric-label">存疑件</div>
        </div>
        <div className="metric-card">
          <div className="metric-value">{s.total ?? 0}</div>
          <div className="metric-label">累计</div>
        </div>
      </div>

      {data.items?.length === 0 && <div className="panel-empty">收件箱为空（分类器放行的动作已自动执行）</div>}

      {data.items?.map((item) => (
        <div key={item.item_id} className="approval-item">
          <div className="approval-head">
            <span className={`risk-badge risk-${item.verdict_decision === 'block' ? 'red' : 'yellow'}`}>
              {item.verdict_decision === 'block' ? '⛔ 拦截' : '⚠️ 存疑'}
            </span>
            <code className="approval-cmd">{item.command}</code>
          </div>
          <div className="approval-reason">{item.reason}</div>
          {editing === item.item_id ? (
            <div className="approval-edit">
              <input
                className="edit-input"
                value={editCmd}
                onChange={(e) => setEditCmd(e.target.value)}
                placeholder="修改后的命令…"
              />
              <button className="btn btn-primary" onClick={() => { respond(item.item_id, 'edit', editCmd); setEditing(null) }}>
                提交修改
              </button>
              <button className="btn" onClick={() => setEditing(null)}>取消</button>
            </div>
          ) : (
            <div className="approval-actions">
              <button className="btn btn-success" onClick={() => respond(item.item_id, 'approve')}>批准执行</button>
              <button className="btn btn-warn" onClick={() => { setEditing(item.item_id); setEditCmd(item.command) }}>修改命令</button>
              <button className="btn btn-danger" onClick={() => respond(item.item_id, 'reject')}>拒绝</button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
