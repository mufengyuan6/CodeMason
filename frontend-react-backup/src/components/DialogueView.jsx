import { useMemo, useRef, useEffect, useState } from 'react'

/**
 * 对话投影视图（v1.29 产品形态 · 折叠投影 minimal）
 * - 对话本身是事件流的一部分（UserTurnStart/AgentMessageContentDelta 同一 EventLog）
 * - 对话视图 = 事件流折叠投影：UserTurnStart/AgentMessage 折叠成对话气泡 minimal
 *   + 工具调用/审批/验证折叠成可展开卡片 rich（读对话像读 Claude Code，卡片可下钻）
 * - 同一事件流两种渲染模式（对话视图 vs 时间线展开投影），切换不丢上下文
 * - 审批卡在对话视图可原地展开批准（审批禁止移出事件流约束同样适用）
 * - 非新 tab 非新页面（v1.14 防退化约束不变）——面板是视图，投影是真相
 */
export default function DialogueView({ events, sendApproval }) {
  const scrollRef = useRef(null)
  const [confirmId, setConfirmId] = useState(null)
  const [editing, setEditing] = useState(null)
  const [editValue, setEditValue] = useState('')
  const [openDetails, setOpenDetails] = useState(new Set())

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [events.length])

  /**
   * 折叠投影：把同一事件流按"会话轮次"折叠
   * - 对话气泡 minimal：TurnStarted + AgentMessageContentDelta（合并流式文本）
   * - 工具卡片 rich：ExecApprovalRequest / ItemCompleted / Rollback / Error / RootCauseReport
   */
  const turns = useMemo(() => {
    const result = []
    let current = null
    for (const ev of events) {
      if (ev.type === 'TurnStarted') {
        current = { turnIndex: ev.turn_index, mode: ev.mode, sessionId: ev.session_id, messages: [], items: [] }
        result.push(current)
      } else if (ev.type === 'AgentMessageContentDelta' || ev.type === 'UserTurnStart') {
        if (!current) {
          current = { turnIndex: 0, mode: 'act', sessionId: '', messages: [], items: [] }
          result.push(current)
        }
        // 用户消息（Op 上行不入 EventLog，这里只兜底）
        if (ev.type === 'UserTurnStart') {
          current.messages.push({ role: 'user', text: ev.content, id: ev.id })
        } else {
          const last = current.messages[current.messages.length - 1]
          if (last && last.role === 'assistant' && last.id === ev.id) {
            last.text += ev.delta || ''
            if (ev.complete) last.complete = true
          } else {
            current.messages.push({ role: 'assistant', text: ev.delta || '', complete: ev.complete, id: ev.id })
          }
        }
      } else {
        if (!current) {
          current = { turnIndex: 0, mode: 'act', sessionId: '', messages: [], items: [] }
          result.push(current)
        }
        current.items.push(ev)
      }
    }
    return result
  }, [events])

  function toggleDetail(id) {
    setOpenDetails((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleApproval(ev, decision) {
    if (decision === 'approve' && ev.risk_level === 'red') {
      if (confirmId !== ev.approval_id) {
        setConfirmId(ev.approval_id)
        setTimeout(() => setConfirmId((id) => (id === ev.approval_id ? null : id)), 3000)
        return
      }
      setConfirmId(null)
    }
    sendApproval?.(ev.approval_id, decision)
  }

  function handleEditSubmit(ev) {
    sendApproval?.(ev.approval_id, 'edit', editValue || ev.command)
    setEditing(null)
    setEditValue('')
  }

  function renderApprovalActions(ev) {
    if (editing === ev.approval_id) {
      return (
        <div className="ev-approval-edit">
          <textarea
            className="appr-edit-input"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            rows={2}
            autoFocus
          />
          <button className="btn btn-edit" onClick={() => handleEditSubmit(ev)}>提交修改</button>
          <button className="btn btn-ghost" onClick={() => setEditing(null)}>取消</button>
        </div>
      )
    }
    return (
      <div className="ev-approval-actions">
        <button className="btn btn-approve" onClick={() => handleApproval(ev, 'approve')} data-testid="dlg-approve">
          {confirmId === ev.approval_id ? '再次确认' : '批准'}
        </button>
        <button className="btn btn-reject" onClick={() => handleApproval(ev, 'reject')} data-testid="dlg-reject">拒绝</button>
        <button className="btn btn-edit" onClick={() => { setEditing(ev.approval_id); setEditValue(ev.command) }}>编辑</button>
        {ev.risk_level === 'red' && <span className="risk-tag red">高危</span>}
      </div>
    )
  }

  /** 工具卡片 rich：可展开下钻完整事件（含 rationale） */
  function renderItemCard(ev) {
    const open = openDetails.has(ev.id)
    let badge = '系统消息'
    let badgeStyle = {}
    let title = ev.type
    let sub = ''
    switch (ev.type) {
      case 'ExecApprovalRequest':
        badge = '审批'
        badgeStyle = { background: 'var(--c-warning)' }
        title = ev.tool_name
        sub = ev.description
        break
      case 'ItemCompleted':
        badge = ev.item_type === 'turn_summary' ? '总结' : '完成'
        badgeStyle = { background: 'var(--c-success)' }
        title = ev.item_type
        sub = typeof ev.content === 'string' ? ev.content.slice(0, 120) : ''
        break
      case 'Rollback':
        badge = '回滚'
        title = `已回滚到 Checkpoint ${ev.checkpoint_id}`
        break
      case 'Error':
        badge = '错误'
        title = ev.message
        break
      case 'RootCauseReport':
        badge = '溯源'
        badgeStyle = { background: 'var(--c-accent, #7c5cff)' }
        title = `溯源报告 ${ev.report_id}`
        sub = `${ev.trigger} · ${ev.status === 'degraded' ? '纯确定性' : '完整链路'} · ${(ev.stages || []).length} 阶段定位`
        break
      default:
        break
    }
    return (
      <div className="dlg-item-card" key={ev.id} data-testid="dlg-item-card">
        <button className="dlg-item-head" onClick={() => toggleDetail(ev.id)} data-testid={`dlg-item-${ev.id}`}>
          <span className="ev-badge" style={badgeStyle}>{badge}</span>
          <span className="dlg-item-title">{title}</span>
          {sub && <span className="dlg-item-sub">{sub}</span>}
          <span className="dlg-chevron">{open ? '▾' : '▸'}</span>
        </button>
        {open && (
          <div className="dlg-item-body">
            {ev.type === 'ExecApprovalRequest' && ev.command && <code className="ev-command">{ev.command}</code>}
            {ev.type === 'ExecApprovalRequest' && renderApprovalActions(ev)}
            {ev.type === 'ItemCompleted' && ev.metrics && (
              <div className="ev-metrics">
                {Object.entries(ev.metrics).slice(0, 4).map(([k, v]) => (
                  <span key={k} className="metric-chip">{k}: {v}</span>
                ))}
              </div>
            )}
            {ev.type === 'RootCauseReport' && (
              <div className="dlg-rc-report">
                {ev.fix_instructions?.length > 0 && (
                  <div className="dlg-rc-section">
                    <b>修复指令</b>
                    <ul>{ev.fix_instructions.map((ins, i) => <li key={i}>{ins}</li>)}</ul>
                  </div>
                )}
                {ev.stages?.length > 0 && (
                  <div className="dlg-rc-section">
                    <b>阶段定位</b>
                    <ul>{ev.stages.map((s, i) => <li key={i}>[{s.stage}] {s.issue}</li>)}</ul>
                  </div>
                )}
                {ev.attributions?.length > 0 && (
                  <div className="dlg-rc-section">
                    <b>归因假设</b>
                    <ul>
                      {ev.attributions.map((a, i) => (
                        <li key={i}>{a.hypothesis}
                          {a.agent_inferred && <span className="metric-chip">agent_inferred</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {ev.rationale && (
              <div className="dlg-rationale" data-testid="dlg-rationale">
                <span className="metric-chip">理由</span>
                <span className="dlg-rationale-text">{ev.rationale}</span>
                <span className="dlg-rationale-src">（模型自述理由，非验证事实）</span>
              </div>
            )}
            {ev.type !== 'ExecApprovalRequest' && ev.type !== 'ItemCompleted' && ev.type !== 'RootCauseReport' && (
              <pre className="diff-pre diff-pre-inline"><code>{JSON.stringify(ev, null, 2).slice(0, 500)}</code></pre>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="timeline dialogue-view" ref={scrollRef} data-testid="dialogue-view">
      {turns.length === 0 && (
        <div className="timeline-empty">
          <p>对话为空</p>
          <p className="hint">在下方输入任务，对话会显示在这里</p>
        </div>
      )}
      {turns.map((turn, ti) => (
        <div className="dlg-turn" key={ti} data-testid="dlg-turn">
          <div className="dlg-turn-rail">
            <span className="dlg-turn-num">{turn.turnIndex}</span>
          </div>
          <div className="dlg-turn-body">
            {turn.messages.map((m, mi) => (
              <div key={`${m.id}-${mi}`} className={`dlg-msg ${m.role === 'user' ? 'is-user' : 'is-agent'}`} data-testid="dlg-msg">
                <div className={`dlg-bubble ${m.role === 'user' ? 'is-user' : 'is-agent'}`}>
                  {m.role === 'user' ? m.text : (m.text || (m.complete ? '' : '思考中…'))}
                  {m.role === 'agent' && !m.complete && <span className="breath-dots"><i /><i /><i /></span>}
                </div>
              </div>
            ))}
            {turn.items.map((ev) => renderItemCard(ev))}
          </div>
        </div>
      ))}
    </div>
  )
}
