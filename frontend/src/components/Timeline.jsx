import { useEffect, useMemo, useRef } from 'react'

/**
 * 事件流时间线（Event Queue 渲染）
 * - WebSocket 推送，卡片流式渲染
 * - Agent 思考呼吸点 / 工具调用折叠展开
 * - 新事件 spring 滑入（stagger 40ms）
 */
export default function Timeline({ events }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [events.length])

  const groups = useMemo(() => {
    // 按 message_index 聚合 delta 流式消息
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

  function renderCard(ev) {
    switch (ev.type) {
      case 'TurnStarted':
        return (
          <div className="ev-card ev-turn" data-testid="ev-turn">
            <span className="ev-badge">回合</span>
            <div className="ev-body">
              <div className="ev-title">Turn #{ev.turn_index}</div>
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
          <div className="ev-card ev-approval" data-testid="ev-approval">
            <span className="ev-badge" style={{ background: 'var(--c-warning)' }}>审批</span>
            <div className="ev-body">
              <div className="ev-title">{ev.tool_name}</div>
              <div className="ev-sub">{ev.description}</div>
              <code className="ev-command">{ev.command}</code>
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
        return <div className="ev-card ev-raw"><div className="ev-body">{JSON.stringify(ev).slice(0, 160)}</div></div>
    }
  }

  return (
    <div className="timeline" ref={scrollRef} data-testid="timeline">
      {groups.length === 0 && (
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
