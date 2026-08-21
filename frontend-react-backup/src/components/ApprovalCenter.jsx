import { useState } from 'react'

/**
 * 审批中心（人类在环 · G5）
 * - staging diff 双栏对比（左旧右新）
 * - 批准 / 拒绝 / 编辑后批准
 * - 高危命令二次确认（点击后 3s 内再点一次）
 */
export default function ApprovalCenter({ approvals, onDecision, onViewFile }) {
  const [confirmId, setConfirmId] = useState(null)
  const [editing, setEditing] = useState(null)
  const [editValue, setEditValue] = useState('')

  function handleDecision(a, decision) {
    if (decision === 'approve' && a.risk_level === 'red') {
      // 高危操作二次确认
      if (confirmId !== a.approval_id) {
        setConfirmId(a.approval_id)
        setTimeout(() => setConfirmId((id) => (id === a.approval_id ? null : id)), 3000)
        return
      }
      setConfirmId(null)
    }
    onDecision(a.approval_id, decision)
  }

  function handleEditSubmit(a) {
    onDecision(a.approval_id, 'edit', editValue || a.command)
    setEditing(null)
    setEditValue('')
  }

  if (approvals.length === 0) {
    return (
      <div className="approval-center empty" data-testid="approval-center">
        <p>审批队列为空</p>
        <p className="hint">危险操作会在这里等待你的确认</p>
      </div>
    )
  }

  return (
    <div className="approval-center" data-testid="approval-center">
      <h3 className="module-title">审批中心 <span className="count-pill">{approvals.length}</span></h3>
      {approvals.map((a) => (
        <div key={a.approval_id} className={`approval-card risk-${a.risk_level}`} data-testid="approval-card">
          <div className="appr-header">
            <span className={`risk-tag ${a.risk_level}`}>{a.risk_level === 'red' ? '高危' : a.risk_level === 'yellow' ? '中危' : '低危'}</span>
            <span className="appr-tool">{a.tool_name}</span>
            <span className="appr-desc">{a.description}</span>
          </div>

          {a.command && <code className="appr-command">{a.command}</code>}

          {a.diff_preview && (
            <div className="diff-viewer" data-testid="diff-preview">
              <pre className="diff-pre"><code>{a.diff_preview}</code></pre>
            </div>
          )}

          {editing === a.approval_id ? (
            <div className="appr-edit">
              <textarea
                className="appr-edit-input"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                rows={2}
                autoFocus
              />
              <button className="btn btn-edit" onClick={() => handleEditSubmit(a)}>提交修改</button>
              <button className="btn btn-ghost" onClick={() => setEditing(null)}>取消</button>
            </div>
          ) : (
            <div className="appr-actions">
              <button
                className="btn btn-approve"
                onClick={() => handleDecision(a, 'approve')}
                data-testid="appr-approve"
              >
                {confirmId === a.approval_id ? '再次确认' : '批准'}
              </button>
              <button className="btn btn-reject" onClick={() => handleDecision(a, 'reject')} data-testid="appr-reject">拒绝</button>
              <button className="btn btn-edit" onClick={() => { setEditing(a.approval_id); setEditValue(a.command) }}>编辑</button>
              {onViewFile && (
                <button className="btn btn-ghost" onClick={() => onViewFile(a)}>查看上下文</button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
