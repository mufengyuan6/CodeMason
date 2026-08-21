import { useEffect, useState } from 'react'

/**
 * 交付横幅（峰终定律：任务完成是关键时刻，反馈放大）
 * - 最近一次 ItemCompleted 的 metrics 展示（用户语言：L12 翻译）
 * - 数字滚动入场（多巴胺）
 * - 输入框聚焦提示"下一个任务"
 */

// metrics key → 用户语言（G-32：YAGNI 等黑话不上 UI）
const METRIC_LABELS = {
  lines_reduced: '少写代码',
  deps_added: '新增依赖',
  duplicates_found: '检出重复',
  readability_ok: '可读性达标',
}

function fmtValue(key, v) {
  if (key === 'readability_ok') return v ? '✓' : '—'
  if (typeof v === 'number') return Math.round(v)
  return v
}

export default function DeliveryBanner({ metrics, onClose }) {
  const [shown, setShown] = useState(0)

  useEffect(() => {
    if (!metrics) return
    let raf
    const target = 1
    const start = performance.now()
    const dur = 400
    function tick(now) {
      const p = Math.min(1, (now - start) / dur)
      setShown(p)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [metrics])

  if (!metrics) return null

  const entries = Object.entries(metrics)
  const saved = metrics.lines_reduced

  return (
    <div className="delivery-banner" data-testid="delivery-banner">
      <div className="delivery-head">
        <span className="delivery-badge">任务完成</span>
        <button className="delivery-close" onClick={onClose} aria-label="关闭交付横幅" data-testid="delivery-close">
          ×
        </button>
      </div>
      {saved > 0 && (
        <p className="delivery-summary">
          本次少写 <b>{Math.round(saved * shown)}</b> 行代码
        </p>
      )}
      {entries.length > 0 && (
        <div className="delivery-metrics">
          {entries.map(([k, v]) => (
            <div key={k} className="delivery-metric">
              <span className="delivery-metric-label">{METRIC_LABELS[k] || k}</span>
              <span className="delivery-metric-value">{fmtValue(k, typeof v === 'number' ? v * shown : v)}</span>
            </div>
          ))}
        </div>
      )}
      <p className="delivery-hint">在下方输入下一个任务</p>
    </div>
  )
}
