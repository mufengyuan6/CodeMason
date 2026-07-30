import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchSessions, switchSession, useCockpit } from '../api.js'
import Timeline from './Timeline.jsx'
import DialogueView from './DialogueView.jsx'
import SessionRail from './SessionRail.jsx'
import ConfirmDialog from './ConfirmDialog.jsx'
import ThemePanel from './ThemePanel.jsx'
import YagniBoard from './YagniBoard.jsx'
import FileViewer from './FileViewer.jsx'
import CostCockpit from './CostCockpit.jsx'
import ContextPanel from './ContextPanel.jsx'
import HealthSignals from './HealthSignals.jsx'
import ContributionPanel from './ContributionPanel.jsx'
import ApprovalInboxPanel from './ApprovalInboxPanel.jsx'
import VisionPanel from './VisionPanel.jsx'
import RootCausePanel from './RootCausePanel.jsx'

const HEALTH_POLL_MS = 15000
const VIEW_KEY = 'codemason-center-view' // v1.29：对话视图/时间线双投影切换记忆

/**
 * Cockpit 驾驶舱工作台（三区 · 主流程内联 + 监控抽屉）
 * - 左：会话栏（列表/新建/恢复，对标 pi-web）
 * - 中：事件流主流程（审批内联可操作 + 交付横幅 + 任务输入）
 *   v1.29：中区右上角"对话视图/时间线"双投影切换（对话是入口投影，时间线是审计投影）
 * - 右：监控抽屉（文件/YAGNI/成本/上下文/健康/贡献/收件箱/视觉/溯源，需要才打开）
 * - 压缩/取消二次确认；模式切换带解释；健康信号被动横幅
 * - 主题系统（换壁纸式个性化）：顶栏 ⚙️ 打开主题面板
 */
export default function Cockpit({ onBack }) {
  const [activeSession, setActiveSession] = useState(() => {
    try {
      return sessionStorage.getItem('codemason-session') || 'web'
    } catch {
      return 'web'
    }
  })
  // v1.29：双投影切换（默认时间线=审计投影，对话视图=入口投影，sessionStorage 记忆）
  const [centerView, setCenterView] = useState(() => {
    try {
      return sessionStorage.getItem(VIEW_KEY) || 'timeline'
    } catch {
      return 'timeline'
    }
  })
  const [sessions, setSessions] = useState([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('act')
  const [approvals, setApprovals] = useState([])
  const [viewerFile, setViewerFile] = useState(null)
  const [drawerView, setDrawerView] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [modeHint, setModeHint] = useState(false)
  const [health, setHealth] = useState(null)
  const [sessionsError, setSessionsError] = useState('')
  const [themeOpen, setThemeOpen] = useState(false)
  const modeHintTimer = useRef(null)

  const { connected, events, sendTurn, sendApproval, cancelTurn, compact } = useCockpit({
    sessionId: activeSession,
    onEvent: (ev) => {
      if (ev.type === 'ExecApprovalRequest') {
        setApprovals((prev) => [...prev.filter((a) => a.approval_id !== ev.approval_id), ev])
      }
    },
  })

  /* 会话列表加载 */
  useEffect(() => {
    fetchSessions()
      .then(setSessions)
      .catch(() => setSessionsError('会话列表加载失败'))
  }, [activeSession])

  /* 健康信号轮询（被动横幅，不打断主流程） */
  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const res = await fetch('/health-signals', { headers: { 'x-agent-token': 'demo-token' } })
        if (res.ok) {
          const data = await res.json()
          if (alive) setHealth(data.enabled ? data.report : null)
        }
      } catch {
        /* 静默：健康面板未挂载不打扰 */
      }
    }
    poll()
    const t = setInterval(poll, HEALTH_POLL_MS)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

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

  function handleModeSwitch(next) {
    setMode(next)
    setModeHint(true)
    if (modeHintTimer.current) clearTimeout(modeHintTimer.current)
    modeHintTimer.current = setTimeout(() => setModeHint(false), 3000)
  }

  const handleSessionSelect = useCallback(async (sid) => {
    if (sid === activeSession) return
    try {
      await switchSession(sid)
      setActiveSession(sid)
      try {
        sessionStorage.setItem('codemason-session', sid)
      } catch {
        /* ignore */
      }
      setApprovals([])
      setDrawerView(null)
    } catch {
      setSessionsError('切换会话失败')
    }
  }, [activeSession])

  const handleSessionNew = useCallback(async (name) => {
    try {
      await switchSession(name)
      setActiveSession(name)
      try {
        sessionStorage.setItem('codemason-session', name)
      } catch {
        /* ignore */
      }
      setApprovals([])
      setDrawerView(null)
    } catch {
      setSessionsError('创建会话失败')
    }
  }, [])

  const healthBad = health && (health.status === 'degraded' || health.status === 'critical')

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
        <button className="icon-btn" onClick={() => setConfirm({ type: 'compact' })} title="释放空间（压缩历史记录）" aria-label="释放空间（压缩历史记录）" data-testid="cp-compact">
          ⚡
        </button>
        <button className="icon-btn" onClick={() => setConfirm({ type: 'cancel' })} title="停止当前任务" aria-label="停止当前任务" data-testid="cp-cancel">
          ■
        </button>
        <button className="icon-btn" onClick={() => setThemeOpen((v) => !v)} title="主题设置" aria-label="主题设置" data-testid="cp-theme">
          ⚙
        </button>
      </header>

      {/* 健康被动横幅（不打断主流程） */}
      {healthBad && (
        <div className="health-banner" data-testid="health-banner">
          <span className="health-banner-dot" />
          会话健康度 {health.status === 'critical' ? '严重下降' : '下降'}（连续卡住 {health.stuck_count ?? 0} 次），建议开新会话
          <button className="health-banner-close" onClick={() => setHealth(null)} aria-label="关闭健康提示">×</button>
        </div>
      )}

      {/* 三区主体 */}
      <div className="cockpit-main">
        <SessionRail sessions={sessions} activeId={activeSession} onSelect={handleSessionSelect} onNew={handleSessionNew} />

        <main className="cp-center">
          {/* v1.29 双投影切换器：对话视图（折叠投影）/ 时间线（展开投影）——同一事件流两种渲染 */}
          <div className="cp-center-switcher" data-testid="center-view-switcher">
            <button
              className={`view-btn ${centerView === 'dialogue' ? 'is-active' : ''}`}
              onClick={() => {
                setCenterView('dialogue')
                try { sessionStorage.setItem(VIEW_KEY, 'dialogue') } catch { /* ignore */ }
              }}
              data-testid="view-dialogue"
            >
              对话视图
            </button>
            <button
              className={`view-btn ${centerView === 'timeline' ? 'is-active' : ''}`}
              onClick={() => {
                setCenterView('timeline')
                try { sessionStorage.setItem(VIEW_KEY, 'timeline') } catch { /* ignore */ }
              }}
              data-testid="view-timeline"
            >
              时间线
            </button>
          </div>
          {centerView === 'dialogue'
            ? <DialogueView events={events} sendApproval={sendApproval} />
            : <Timeline events={events} sendApproval={sendApproval} />}
          <footer className="cp-inputbar">
            <div className="cp-mode-switch">
              <button className={`mode-btn ${mode === 'act' ? 'is-active' : ''}`} onClick={() => handleModeSwitch('act')} data-testid="mode-act">
                执行
              </button>
              <button className={`mode-btn ${mode === 'plan' ? 'is-active' : ''}`} onClick={() => handleModeSwitch('plan')} data-testid="mode-plan">
                规划
              </button>
            </div>
            {modeHint && (
              <div className="mode-hint" data-testid="mode-hint">
                {mode === 'plan' ? '规划：只读模式，Agent 不改任何文件，先出方案' : '执行：Agent 可读写文件并运行命令（危险操作需审批）'}
              </div>
            )}
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
        </main>

        {/* 右：监控抽屉 */}
        <aside className="cp-drawer">
          <div className="drawer-tabs">
            {[
              { id: 'files', label: '文件', icon: '☰' },
              { id: 'yagni', label: 'YAGNI', icon: '▣' },
              { id: 'costs', label: '成本', icon: '⚡' },
              { id: 'context', label: '上下文', icon: '🧠' },
              { id: 'health', label: '健康', icon: '💓' },
              { id: 'contribution', label: '贡献', icon: '📜' },
              { id: 'inbox', label: '收件箱', icon: '📥' },
              { id: 'vision', label: '视觉', icon: '👁' },
              { id: 'rootcause', label: '溯源', icon: '🔍' },
            ].map((m) => (
              <button
                key={m.id}
                className={`drawer-tab ${drawerView === m.id ? 'is-active' : ''}`}
                onClick={() => setDrawerView((v) => (v === m.id ? null : m.id))}
                data-testid={`drawer-${m.id}`}
                aria-label={m.label}
              >
                <span className="drawer-icon">{m.icon}</span>
                <span className="drawer-label">{m.label}</span>
              </button>
            ))}
          </div>
          {drawerView && (
            <div className="drawer-panel" data-testid="drawer-panel">
              {drawerView === 'files' && (
                <FileViewer file={viewerFile} events={events} onSelect={setViewerFile} />
              )}
              {drawerView === 'yagni' && <YagniBoard metrics={yagniMetrics} />}
              {drawerView === 'costs' && <CostCockpit />}
              {drawerView === 'context' && <ContextPanel />}
              {drawerView === 'health' && <HealthSignals />}
              {drawerView === 'contribution' && <ContributionPanel />}
              {drawerView === 'inbox' && <ApprovalInboxPanel />}
              {drawerView === 'vision' && <VisionPanel events={events} />}
              {drawerView === 'rootcause' && <RootCausePanel events={events} />}
            </div>
          )}
        </aside>
      </div>

      {/* 确认弹层：压缩 / 取消 */}
      {confirm?.type === 'compact' && (
        <ConfirmDialog
          title="释放空间？"
          desc="整理本会话的早期记录，让 Agent 响应更快。历史记录会完整保留，可随时回看。"
          confirmLabel="释放空间"
          onConfirm={() => { compact('session'); setConfirm(null) }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm?.type === 'cancel' && (
        <ConfirmDialog
          title="停止当前任务？"
          desc="Agent 正在执行的操作将终止。已完成的修改会保留，不会丢失。"
          confirmLabel="停止任务"
          onConfirm={() => { cancelTurn('user cancelled from cockpit'); setConfirm(null) }}
          onCancel={() => setConfirm(null)}
        />
      )}

      {/* 主题面板（换壁纸式个性化） */}
      {themeOpen && <ThemePanel onClose={() => setThemeOpen(false)} />}
    </div>
  )
}
