import { useEffect, useState } from 'react'
import { THEMES, applyTheme, loadTheme } from '../theme.js'

/**
 * 主题面板（换壁纸式个性化，对标 Codex 换皮市场）
 * - 3 套内置主题 swatch 选择
 * - 自定义主色/强调色 color picker
 * - localStorage 持久化
 */
export default function ThemePanel({ onClose }) {
  const [cfg, setCfg] = useState(() => loadTheme())

  useEffect(() => {
    applyTheme(cfg)
  }, [cfg])

  function pickTheme(id) {
    setCfg((c) => ({ ...c, id }))
  }

  function setCustom(key, value) {
    setCfg((c) => ({ ...c, [key]: value }))
  }

  function reset() {
    setCfg({ id: 'jade-gold', primary: null, accent: null })
  }

  return (
    <div className="theme-overlay" data-testid="theme-panel" onClick={onClose}>
      <div className="theme-panel" onClick={(e) => e.stopPropagation()}>
        <div className="theme-head">
          <h3 className="theme-title">主题</h3>
          <button className="theme-close" onClick={onClose} aria-label="关闭主题面板" data-testid="theme-close">
            ×
          </button>
        </div>

        <div className="theme-grid">
          {THEMES.map((t) => (
            <button
              key={t.id}
              className={`theme-card ${cfg.id === t.id ? 'is-active' : ''}`}
              onClick={() => pickTheme(t.id)}
              data-testid={`theme-${t.id}`}
            >
              <span className="theme-swatches">
                {t.swatch.map((c) => (
                  <span key={c} className="theme-swatch" style={{ background: c }} />
                ))}
              </span>
              <span className="theme-name">{t.name}</span>
              <span className="theme-desc">{t.desc}</span>
            </button>
          ))}
        </div>

        <div className="theme-custom">
          <span className="theme-custom-label">自定义颜色</span>
          <label className="theme-pick">
            主色
            <input
              type="color"
              value={cfg.primary || '#10B981'}
              onChange={(e) => setCustom('primary', e.target.value)}
              data-testid="theme-primary-pick"
            />
          </label>
          <label className="theme-pick">
            强调色
            <input
              type="color"
              value={cfg.accent || '#EAB308'}
              onChange={(e) => setCustom('accent', e.target.value)}
              data-testid="theme-accent-pick"
            />
          </label>
        </div>

        <div className="theme-foot">
          <button className="btn btn-ghost btn-xs" onClick={reset} data-testid="theme-reset">
            恢复默认
          </button>
        </div>
      </div>
    </div>
  )
}
