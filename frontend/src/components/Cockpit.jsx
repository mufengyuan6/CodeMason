import { useMemo, useState } from 'react'
import { useCockpit } from '../api.js'
import Timeline from './Timeline.jsx'
import ApprovalCenter from './ApprovalCenter.jsx'
import YagniBoard from './YagniBoard.jsx'
import FileViewer from './FileViewer.jsx'

/**
 * Cockpit 驾驶舱主界面（蓝粉视觉 · 核心 4 模块）
 * - 事件流时间线（WS 推送，卡片流式渲染）
 * - 审批中心（diff 视图 + 批准/拒绝 + auto-approve）
 * - YAGNI 看板（四维量化指标）
 * - 会话输入 + 提示排队（agent 忙时输入自动入队）
 */
export default function Cockpit({ onBack }) {
  const [activeModule, setActiveModule] = useState('timeline')
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('act')
  const [approvals, setApprovals] = useState([])
  const [viewerFile, setViewerFile] = useState(null)

  const { connected, events, sendTurn, sendApproval, cancelTurn, compact } = useCockpit({
    sessionId: 'web',
    onEvent: (ev) => {
      if (ev.type === 'ExecApprovalRequest') {
        setApprovals((prev) => [...prev.filter((a) => a.approval_id !== ev.approval_id), ev])
      }
    },
  })

  const yagniMetrics = useMemo(() => {
    return events
      .filter((e) => e.type === 'ItemCompleted' && e.metrics)
      .map((e) => e.metrics)
  }, [events])

  function handleSend() {
    const text = input.trim()
    if (!text) return
    sendTurn(text, { mode })
    setInput('')
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="cockpit" data-testid="cockpit">
      {/* 顶栏 */}
      <header className="cockpit-topbar">
        <button className="icon-btn" onClick={onBack} data-testid="cp-back" aria-label="返回">
          ←
        </button>
        <div className="cp-brand">
          <span className="cp-logo">CM</span>
          <span className="cp-title">CodeMason 驾驶舱</span>
        </div>
        <div className={`cp-status ${connected ? 'is-online' : 'is-offline'}`}>
          <span className="status-dot" />
          {connected ? '内核已连接' : '重连中…'}
        </div>
        <button className="icon-btn" onClick={() => compact('session')} aria-label="压缩上下文">
          ⚡
        </button>
        <button className="icon-btn" onClick={() => cancelTurn('user cancelled from cockpit')} aria-label="取消当前回合">
          ■
        </button>
      </header>

      {/* 模块导航 */}
      <nav className="cp-nav" data-testid="cp-nav">
        {[
          { id: 'timeline', label: '事件流', icon: '≋' },
          { id: 'approvals', label: '审批', icon: '✓', badge: approvals.length },
          { id: 'yagni', label: 'YAGNI', icon: '▣' },
          { id: 'files', label: '文件', icon: '☰' },
        ].map((m) => (
          <button
            key={m.id}
            className={`cp-nav-item ${activeModule === m.id ? 'is-active' : ''}`}
            onClick={() => setActiveModule(m.id)}
            data-testid={`cp-module-${m.id}`}
            aria-label={m.label}
          >
            <span className="nav-icon">{m.icon}</span>
            <span className="nav-label">{m.label}</span>
            {m.badge > 0 && <span className="nav-badge">{m.badge}</span>}
          </button>
        ))}
      </nav>

      {/* 主内容区 */}
      <main className="cp-main">
        {activeModule === 'timeline' && <Timeline events={events} />}
        {activeModule === 'approvals' && (
          <ApprovalCenter approvals={approvals} onDecision={sendApproval} onViewFile={setViewerFile} />
        )}
        {activeModule === 'yagni' && <YagniBoard metrics={yagniMetrics} />}
        {activeModule === 'files' && (
          <FileViewer file={viewerFile} events={events} onSelect={setViewerFile} />
        )}
      </main>

      {/* 底部输入区：模式切换 + 提示排队 */}
      <footer className="cp-inputbar">
        <div className="cp-mode-switch">
          <button className={`mode-btn ${mode === 'act' ? 'is-active' : ''}`} onClick={() => setMode('act')}>
            执行
          </button>
          <button className={`mode-btn ${mode === 'plan' ? 'is-active' : ''}`} onClick={() => setMode('plan')}>
            规划
          </button>
        </div>
        <textarea
          className="cp-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入任务描述…（Agent 忙时自动排队）"
          data-testid="cp-input"
          rows={2}
        />
        <button className="cp-send" onClick={handleSend} data-testid="cp-send" disabled={!input.trim()}>
          发送
        </button>
      </footer>
    </div>
  )
}
