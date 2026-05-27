import { useEffect, useMemo, useRef, useState } from 'react'
import DeliveryBanner from './DeliveryBanner.jsx'

const GUIDE_KEY = 'codemason-guide-dismissed'

/**
 * 事件流时间线（Event Queue 渲染 · 主流程）
 * - WebSocket 推送，卡片流式渲染
 * - 审批卡【内联可操作】：阻塞点即时可见，批准/拒绝/编辑 + 高危二次确认
 * - 交付横幅：任务完成放大反馈（峰终定律）
 * - 首次进入 3 步引导卡（localStorage 记忆，老用户跳过）
 */
export default function Timeline({ events, sendApproval }) {
  const scrollRef = useRef(null)
  const [guideDismissed, setGuideDismissed] = useState(() => localStorage.getItem(GUIDE_KEY) === '1')
  const [confirmId, setConfirmId] = useState(null)
  const [editing, setEditing] = useState(null)
  const [editValue, setEditValue] = useState('')

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [events.length])

  const groups = useMemo(() => {
    const messages = new Map()
    const other = []
    for (const ev of events) {
      if (ev.type === 'AgentMessageContentDelta') {
        const key = ev.message_index ?? ev.id
        const cur = messages.get(key) || { key, session_id: ev.session_id, text: '', complete: false, id: ev.id, ts: ev.ts }
        cur.text += ev.delta || ''
        if (ev.complete) cur.complete = true
        messages.set(key, cur)
      } else {
        other.push(ev)
      }
    }
    const merged = [...messages.values(), ...other].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0) || (a.id ?? 0) - (b.id ?? 0))
    return merged
  }, [events])

  const lastDelivery = useMemo(() => {
    const done = events.filter((e) => e.type === 'ItemCompleted' && e.metrics)
    return done.length > 0 ? done[done.length - 1].metrics : null
  }, [events])

  const lastItemType = useMemo(() => {
    const done = events.filter((e) => e.type === 'ItemCompleted')
    return done.length > 0 ? done[done.length - 1].item_type : null
  }, [events])

  function handleApproval(a, decision) {
    if (decision === 'approve' && a.risk_level === 'red') {
      if (confirmId !== a.approval_id) {
        setConfirmId(a.approval_id)
        setTimeout(() => setConfirmId((id) => (id === a.approval_id ? null : id)), 3000)
        return
      }
      setConfirmId(null)
    }
    sendApproval?.(a.approval_id, decision)
  }

  function handleEditSubmit(a) {
    sendApproval?.(a.approval_id, 'edit', editValue || a.command)
    setEditing(null)
    setEditValue('')
  }

  function dismissGuide() {
    localStorage.setItem(GUIDE_KEY, '1')
    setGuideDismissed(true)
  }

  function renderApprovalActions(a) {
    if (editing === a.approval_id) {
      return (
        <div className="ev-approval-edit">
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
      )
    }
    return (
      <div className="ev-approval-actions">
        <button
          className="btn btn-approve"
          onClick={() => handleApproval(a, 'approve')}
          data-testid="ev-approve"
        >
          {confirmId === a.approval_id ? '再次确认' : '批准'}
        </button>
        <button className="btn btn-reject" onClick={() => handleApproval(a, 'reject')} data-testid="ev-reject">拒绝</button>
        <button className="btn btn-edit" onClick={() => { setEditing(a.approval_id); setEditValue(a.command) }}>编辑</button>
        {a.risk_level === 'red' && <span className="risk-tag red">高危</span>}
      </div>
    )
  }

  function renderCard(ev) {
    switch (ev.type) {
      case 'TurnStarted':
        return (
          <div className="ev-card ev-turn" data-testid="ev-turn">
            <span className="ev-badge">回合</span>
            <div className="ev-body">
              <div className="ev-title">回合 #{ev.turn_index}</div>
              <div className="ev-sub">{ev.mode === 'plan' ? '规划模式' : '执行模式'} · session {ev.session_id}</div>
            </div>
          </div>
        )
      case 'AgentMessageContentDelta':
        return (
          <div className={`ev-card ev-msg ${ev.complete ? 'is-complete' : 'is-streaming'}`}>
            <span className="ev-badge">Agent</span>
            <div className="ev-body">
              <div className="ev-msg-text">{ev.text || (ev.complete ? '' : '思考中…')}</div>
              {!ev.complete && <span className="breath-dots"><i /><i /><i /></span>}
            </div>
          </div>
        )
      case 'ExecApprovalRequest':
        return (
          <div className="ev-card ev-approval is-interactive" data-testid="ev-approval">
            <span className="ev-badge" style={{ background: 'var(--c-warning)' }}>审批</span>
            <div className="ev-body">
              <div className="ev-title">{ev.tool_name}</div>
              <div className="ev-sub">{ev.description}</div>
              {ev.command && <code className="ev-command">{ev.command}</code>}
              {ev.diff_preview && (
                <pre className="diff-pre diff-pre-inline"><code>{ev.diff_preview}</code></pre>
              )}
              {renderApprovalActions(ev)}
            </div>
          </div>
        )
      case 'ItemCompleted':
        return (
          <div className="ev-card ev-item">
            <span className="ev-badge" style={{ background: 'var(--c-success)' }}>完成</span>
            <div className="ev-body">
              <div className="ev-title">{ev.item_type}</div>
              {typeof ev.content === 'string' && <div className="ev-sub">{ev.content.slice(0, 200)}</div>}
              {ev.metrics && (
                <div className="ev-metrics">
                  {Object.entries(ev.metrics).slice(0, 4).map(([k, v]) => (
                    <span key={k} className="metric-chip">{k}: {v}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )
      case 'TurnCancelled':
        return <div className="ev-card ev-warn"><span className="ev-badge">取消</span><div className="ev-body">{ev.reason || '回合已取消'}</div></div>
      case 'Rollback':
        return <div className="ev-card ev-warn"><span className="ev-badge">回滚</span><div className="ev-body">已回滚到 Checkpoint {ev.checkpoint_id}</div></div>
      case 'Error':
        return <div className="ev-card ev-error"><span className="ev-badge">错误</span><div className="ev-body">{ev.message}</div></div>
      default:
        // 未知事件：不裸露原始数据（L7 修复），折叠展示
        return (
          <details className="ev-card ev-raw">
            <summary><span className="ev-badge">系统消息</span><span className="ev-sub">点击查看详情</span></summary>
            <div className="ev-body">{JSON.stringify(ev).slice(0, 300)}</div>
          </details>
        )
    }
  }

  return (
    <div className="timeline" ref={scrollRef} data-testid="timeline">
      {/* 交付横幅：峰终反馈（始终置顶，可关闭） */}
      {lastDelivery && (
        <div className="timeline-banner-slot">
          <DeliveryBanner metrics={lastDelivery} itemType={lastItemType} onClose={() => null} />
        </div>
      )}

      {groups.length === 0 && !guideDismissed && (
        <div className="timeline-guide" data-testid="timeline-guide">
          <h3 className="guide-title">三步开始</h3>
          <ol className="guide-list">
            <li><b>发任务</b> —— 在下方输入你想让 Agent 做的事</li>
            <li><b>看每一步</b> —— Agent 的思考与操作实时显示在这里</li>
            <li><b>关键点审批</b> —— 危险操作会在这里等你确认，可批准/拒绝/修改</li>
          </ol>
          <p className="guide-note">一切可取消、可回滚、可审计 —— 改坏了随时回到 Checkpoint</p>
          <button className="btn btn-primary" onClick={dismissGuide} data-testid="guide-start">
            开始使用
          </button>
        </div>
      )}

      {groups.length === 0 && guideDismissed && (
        <div className="timeline-empty">
          <p>事件流为空</p>
          <p className="hint">在下方输入任务，观察 Agent 的每一步</p>
        </div>
      )}

      {groups.map((ev, i) => (
        <div key={ev.id ?? `${ev.type}-${i}`} className="ev-row" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
          <div className="ev-rail" />
          {renderCard(ev)}
        </div>
      ))}
    </div>
  )
}
