import { useState } from 'react'

/**
 * 会话栏（对标 pi-web：会话列表/新建/恢复，按工作目录组织）
 * - 列表 + 事件数（未完成标记由事件数暗示）
 * - 新建会话内联输入
 * - 切换会话 = 换 JSONL 重放（事件溯源）
 */
export default function SessionRail({ sessions, activeId, onSelect, onNew }) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')

  function submit() {
    const n = name.trim()
    if (!n) return
    onNew(n)
    setCreating(false)
    setName('')
  }

  return (
    <aside className="session-rail" data-testid="session-rail">
      <div className="rail-header">
        <span className="rail-title">会话</span>
        <button className="rail-new" onClick={() => setCreating((v) => !v)} aria-label="新建会话" data-testid="session-new">
          {creating ? '×' : '+'}
        </button>
      </div>
      {creating && (
        <div className="rail-create">
          <input
            className="rail-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder="会话名"
            autoFocus
            data-testid="session-name-input"
          />
          <button className="btn btn-primary btn-xs" onClick={submit} data-testid="session-create-ok">
            创建
          </button>
        </div>
      )}
      <div className="rail-list">
        {sessions.length === 0 && <p className="rail-empty">暂无会话</p>}
        {sessions.map((s) => (
          <button
            key={s.session_id}
            className={`rail-item ${s.session_id === activeId ? 'is-active' : ''}`}
            onClick={() => onSelect(s.session_id)}
            data-testid={`session-item-${s.session_id}`}
          >
            <span className="rail-item-name">{s.session_id}</span>
            <span className="rail-item-meta">{s.events} 条</span>
          </button>
        ))}
      </div>
    </aside>
  )
}
